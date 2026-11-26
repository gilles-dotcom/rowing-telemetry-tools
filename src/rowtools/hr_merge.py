#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Union

import numpy as np
from fitparse import FitFile as FitParseFile

from fit_tool.fit_file import FitFile
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile import profile_type as PT


logger = logging.getLogger(__name__)

UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Bounded LRU-style cache: keeps at most _HR_CACHE_MAX_SIZE entries.
_HR_CACHE_MAX_SIZE = 32
HR_SERIES_CACHE: OrderedDict = OrderedDict()
HR_SERIES_CACHE_LOCK = threading.RLock()


def _enum_int(x, default=None):
    if x is None:
        return default
    return int(getattr(x, "value", x))


def to_utc_datetime(ts: Union[int, float, datetime]) -> datetime:
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    raw = float(ts)
    for seconds in (raw, raw * 1e-3, raw * 1e-6):
        try:
            dt = UNIX_EPOCH + timedelta(seconds=seconds)
            if 2000 <= dt.year <= 2035:
                return dt
        except Exception:
            pass
    return UNIX_EPOCH + timedelta(seconds=raw * 1e-3)


def _fit_ts_to_ms(ts) -> int:
    return int(round(to_utc_datetime(ts).timestamp() * 1000))


@dataclass
class Series:
    t: np.ndarray
    v: np.ndarray
    origin_utc: datetime


@dataclass
class BaseRecords:
    timestamps_utc: List[datetime]
    t: np.ndarray
    origin_utc: datetime


def read_record_series_fitparse(path: str, field_candidates: List[str]) -> Series:
    p = Path(path)
    mtime = int(p.stat().st_mtime) if p.exists() else 0
    cache_key = (str(p.resolve()), tuple(field_candidates), mtime)

    with HR_SERIES_CACHE_LOCK:
        cached = HR_SERIES_CACHE.get(cache_key)
        if cached is not None:
            # Move to end to mark as recently used
            HR_SERIES_CACHE.move_to_end(cache_key)
            return cached

    fit = FitParseFile(path)
    times: List[datetime] = []
    vals: List[float] = []

    fields = tuple(field_candidates)

    # Try 'record' messages first, fall back to 'monitoring' and other types
    msg_types = ["record", "monitoring", "monitoring_info", "event"]
    for msg_type in msg_types:
        for msg in fit.get_messages(msg_type):
            ts = msg.get_value("timestamp")
            if ts is None:
                continue

            val = None
            for f in fields:
                v = msg.get_value(f)
                if v is not None:
                    val = v
                    break

            if val is None:
                continue

            try:
                vals.append(float(val))
                times.append(to_utc_datetime(ts))
            except Exception:
                continue

        if times:
            logger.info("read_record_series_fitparse: found %d samples in '%s' messages", len(times), msg_type)
            break

    if not times:
        # Log all available message types and fields for debugging
        fit2 = FitParseFile(path)
        msg_summary = {}
        for msg in fit2.get_messages():
            name = msg.name
            if name not in msg_summary:
                msg_summary[name] = set()
            for f in msg.fields:
                msg_summary[name].add(f.name)
        logger.warning("No HR data found in %s. Available messages: %s",
                       path, {k: list(v) for k, v in msg_summary.items()})
        raise RuntimeError(f"No usable record samples for {field_candidates} in {path}")

    origin = min(times)
    t = np.fromiter(((x - origin).total_seconds() for x in times), dtype=float)
    v = np.asarray(vals, dtype=float)

    if len(t) > 1 and np.any(np.diff(t) < 0):
        order = np.argsort(t)
        t = t[order]
        v = v[order]

    series = Series(t=t, v=v, origin_utc=origin)
    with HR_SERIES_CACHE_LOCK:
        HR_SERIES_CACHE[cache_key] = series
        # Evict oldest entry if over size limit
        while len(HR_SERIES_CACHE) > _HR_CACHE_MAX_SIZE:
            HR_SERIES_CACHE.popitem(last=False)
    return series


def extract_base_records(base_fit: FitFile) -> BaseRecords:
    ts_utc: List[datetime] = []

    for rec in base_fit.records:
        msg = getattr(rec, "message", None)
        if isinstance(msg, RecordMessage):
            ts = getattr(msg, "timestamp", None)
            if ts is None:
                continue
            ts_utc.append(to_utc_datetime(ts))

    if not ts_utc:
        raise RuntimeError("No RecordMessage timestamps found in base FIT.")

    origin = min(ts_utc)
    t = np.array([(x - origin).total_seconds() for x in ts_utc], dtype=float)
    return BaseRecords(
        timestamps_utc=ts_utc,
        t=t,
        origin_utc=origin,
    )


def align_no_hold(
    source_t: np.ndarray,
    source_v: np.ndarray,
    target_t: np.ndarray,
    max_gap_s: float,
) -> np.ndarray:
    """
    Vectorized nearest-neighbor alignment without hold-forward.
    If nearest HR point is farther than max_gap_s, output NaN.
    """
    out = np.full(len(target_t), np.nan, dtype=float)

    if len(source_t) == 0 or len(target_t) == 0:
        return out

    idx = np.searchsorted(source_t, target_t, side="left")

    left_idx = np.clip(idx - 1, 0, len(source_t) - 1)
    right_idx = np.clip(idx, 0, len(source_t) - 1)

    left_gap = np.abs(target_t - source_t[left_idx])
    right_gap = np.abs(target_t - source_t[right_idx])

    choose_right = right_gap < left_gap
    best_idx = np.where(choose_right, right_idx, left_idx)
    best_gap = np.where(choose_right, right_gap, left_gap)

    valid = best_gap <= max_gap_s
    out[valid] = source_v[best_idx[valid]]

    return out


RECORD_COPY_FIELDS = [
    "timestamp",
    "power",
    "cadence",
    "fractional_cadence",
    "distance",
    "speed",
    "enhanced_speed",
    "altitude",
    "enhanced_altitude",
    "grade",
    "position_lat",
    "position_long",
    "calories",
    "cycle_length",
    "cycles",
    "accumulated_power",
    "compressed_speed_distance",
    "gps_accuracy",
    "left_right_balance",
]

POWER_SUMMARY_FIELDS = {"avg_power", "max_power", "normalized_power", "total_work"}


def scale_summary_message(src, power_mult: float):
    """Clone a FIT session/lap summary while scaling power totals."""
    dst = src.__class__()
    for field in getattr(src, "fields", []):
        name = getattr(field, "name", None)
        if not name:
            continue
        try:
            value = getattr(src, name, None)
        except Exception:
            value = None
        if value is None:
            continue
        if name in POWER_SUMMARY_FIELDS:
            try:
                value = int(round(float(value) * power_mult))
            except (TypeError, ValueError):
                pass
        try:
            setattr(dst, name, value)
        except Exception as exc:
            logger.debug("scale_summary_message: could not set %s: %s", name, exc)
    return dst


def _power_work_joules(record_times_ms: List[int], powers: List[float], power_mult: float) -> Optional[int]:
    """Integrate timestamped watts over time and return FIT total_work in joules."""
    if len(record_times_ms) < 2 or len(powers) < 2:
        return None

    work = 0.0
    intervals = 0
    for start_ms, end_ms, start_power, end_power in zip(
        record_times_ms,
        record_times_ms[1:],
        powers,
        powers[1:],
    ):
        elapsed_s = (end_ms - start_ms) / 1000.0
        if elapsed_s <= 0:
            continue
        work += ((start_power + end_power) / 2.0) * elapsed_s
        intervals += 1

    return int(round(work * power_mult)) if intervals else None


def clone_record(src: RecordMessage, hr: Optional[int], temp: Optional[int], power_mult: float) -> RecordMessage:
    dst = RecordMessage()

    for name in RECORD_COPY_FIELDS:
        val = getattr(src, name, None)
        if val is None:
            continue

        if name in {"power", "accumulated_power"}:
            try:
                val = int(round(float(val) * power_mult))
            except Exception as e:
                logger.debug("clone_record: could not scale %s field: %s", name, e)

        try:
            setattr(dst, name, val)
        except Exception as e:
            logger.debug("clone_record: could not set field %r: %s", name, e)

    if hr is not None:
        try:
            dst.heart_rate = int(hr)
        except Exception as e:
            logger.debug("clone_record: could not set heart_rate: %s", e)

    if temp is not None:
        try:
            dst.temperature = int(temp)
        except Exception as e:
            logger.debug("clone_record: could not set temperature: %s", e)

    return dst


def default_out_path(base_fit_path: str) -> str:
    p = Path(base_fit_path)
    return str(p.with_name(f"{p.stem}_merged.fit"))


def merge(
    base_fit_path: str,
    hr_fit_path: str,
    out_path: str,
    offset_arg: str,
    max_gap_s: float,
    merge_temp: bool,
    power_mult: float,
    verify: bool,
    fit_sport: str = "ROWING",
    fit_sub_sport: str = "GENERIC",
):
    base_fit = FitFile.from_file(base_fit_path)
    base = extract_base_records(base_fit)

    hr_series = read_record_series_fitparse(hr_fit_path, ["heart_rate", "hr"])
    temp_series = None
    if merge_temp:
        try:
            temp_series = read_record_series_fitparse(hr_fit_path, ["temperature"])
        except Exception:
            temp_series = None

    if offset_arg == "auto":
        offset_s = (hr_series.origin_utc - base.origin_utc).total_seconds()
    elif offset_arg == "none":
        offset_s = 0.0
    else:
        offset_s = float(offset_arg)

    shifted_hr_t = hr_series.t + offset_s
    hr_on_base = align_no_hold(
        shifted_hr_t,
        hr_series.v,
        base.t,
        max_gap_s=max_gap_s,
    )

    temp_on_base = None
    if temp_series is not None:
        shifted_temp_t = temp_series.t + offset_s
        temp_on_base = align_no_hold(
            shifted_temp_t,
            temp_series.v,
            base.t,
            max_gap_s=max_gap_s,
        )

    try:
        builder = FitFileBuilder(auto_define=True)
    except TypeError:
        builder = FitFileBuilder()

    rec_idx = 0
    hr_written = 0
    first_record_ms = None
    last_record_ms = None
    last_distance = 0.0
    source_session = None
    source_laps = []
    original_powers = []
    record_times_ms = []

    for rec in base_fit.records:
        msg = getattr(rec, "message", None)
        if msg is None:
            continue

        cls_name = msg.__class__.__name__

        # Rebuild summaries/events ourselves, retaining source summary values
        # so total work and power are not silently discarded.
        if cls_name == "SessionMessage":
            source_session = msg
            continue
        if cls_name == "LapMessage":
            source_laps.append(msg)
            continue
        if cls_name in {"ActivityMessage", "EventMessage"}:
            continue

        if isinstance(msg, RecordMessage):
            if getattr(msg, "timestamp", None) is None:
                builder.add(msg)
                continue

            hr_int = None
            temp_int = None

            if rec_idx < len(hr_on_base):
                hv = hr_on_base[rec_idx]
                if not np.isnan(hv):
                    hr_int = int(round(float(hv)))

            if temp_on_base is not None and rec_idx < len(temp_on_base):
                tv = temp_on_base[rec_idx]
                if not np.isnan(tv):
                    temp_int = int(round(float(tv)))

            new_msg = clone_record(msg, hr_int, temp_int, power_mult)
            builder.add(new_msg)
            if getattr(msg, "power", None) is not None:
                try:
                    original_powers.append(float(msg.power))
                    record_times_ms.append(_fit_ts_to_ms(msg.timestamp))
                except (TypeError, ValueError):
                    pass

            ts = getattr(new_msg, "timestamp", None)
            if ts is not None:
                ts_ms = _fit_ts_to_ms(ts)
                if first_record_ms is None:
                    first_record_ms = ts_ms
                last_record_ms = ts_ms

            dist = getattr(new_msg, "distance", None)
            if dist is not None:
                try:
                    last_distance = float(dist)
                except Exception:
                    pass

            if hr_int is not None:
                hr_written += 1

            rec_idx += 1
        else:
            builder.add(msg)

    if first_record_ms is None or last_record_ms is None:
        raise RuntimeError("Merged FIT has no record timestamps")

    elapsed_seconds = max(0.0, (last_record_ms - first_record_ms) / 1000.0)

    # Start event
    ev_start = EventMessage()
    ev_start.timestamp = first_record_ms

    try:
        ev_start.event = _enum_int(PT.Event.TIMER)
        ev_start.event_type = _enum_int(PT.EventType.START)
    except:
        pass

    builder.add(ev_start)

    # Preserve source lap boundaries and all lap-specific summaries. Only the
    # power fields were changed by scale_summary_message(). If the source has
    # no laps, create one summary covering the merged record stream.
    if source_laps:
        for source in source_laps:
            builder.add(scale_summary_message(source, power_mult))
    else:
        lap = LapMessage()
        lap.start_time = first_record_ms
        lap.timestamp = last_record_ms
        lap.total_elapsed_time = elapsed_seconds
        lap.total_timer_time = elapsed_seconds
        try:
            lap.total_distance = float(last_distance)
        except Exception:
            pass
        if getattr(lap, "avg_power", None) is None and original_powers:
            lap.avg_power = int(round(float(np.mean(original_powers)) * power_mult))
        if getattr(lap, "max_power", None) is None and original_powers:
            lap.max_power = int(round(float(np.max(original_powers)) * power_mult))
        total_work = _power_work_joules(record_times_ms, original_powers, power_mult)
        if getattr(lap, "total_work", None) is None and total_work is not None:
            lap.total_work = total_work
        builder.add(lap)

    # Session
    session = scale_summary_message(source_session, power_mult) if source_session is not None else SessionMessage()
    session.start_time = first_record_ms
    session.timestamp = last_record_ms
    session.total_elapsed_time = elapsed_seconds
    session.total_timer_time = elapsed_seconds

    try:
        session.total_distance = float(last_distance)
    except:
        pass

    try:
        sport_val = getattr(PT.Sport, fit_sport.upper(), None) or PT.Sport.ROWING
        session.sport = _enum_int(sport_val)
    except:
        pass

    try:
        sub_sport_val = getattr(PT.SubSport, str(fit_sub_sport or "GENERIC").upper(), None)
        session.sub_sport = _enum_int(sub_sport_val or PT.SubSport.GENERIC)
    except:
        pass

    if getattr(session, "avg_power", None) is None and original_powers:
        session.avg_power = int(round(float(np.mean(original_powers)) * power_mult))
    if getattr(session, "max_power", None) is None and original_powers:
        session.max_power = int(round(float(np.max(original_powers)) * power_mult))
    total_work = _power_work_joules(record_times_ms, original_powers, power_mult)
    if getattr(session, "total_work", None) is None and total_work is not None:
        session.total_work = total_work

    builder.add(session)

    # Stop event
    ev_stop = EventMessage()
    ev_stop.timestamp = last_record_ms

    try:
        ev_stop.event = _enum_int(PT.Event.TIMER)
        ev_stop.event_type = _enum_int(PT.EventType.STOP_ALL)
    except:
        pass

    builder.add(ev_stop)

    # Activity
    activity = ActivityMessage()
    activity.timestamp = last_record_ms

    try:
        activity.type = _enum_int(PT.Activity.MANUAL)
    except:
        pass

    try:
        activity.num_sessions = 1
    except:
        pass

    builder.add(activity)

    merged = builder.build()
    merged.to_file(out_path)

    logger.info("[ok] wrote: %s", out_path)
    logger.info("[info] power start UTC : %s", base.origin_utc.isoformat())
    logger.info("[info] hr    start UTC : %s", hr_series.origin_utc.isoformat())
    logger.info("[info] chosen offset  : %.1fs (HR starts this many seconds into power)", offset_s)
    logger.info("[info] base records   : %d", rec_idx)
    logger.info("[info] HR written     : %d", hr_written)
    logger.info("[info] elapsed sec    : %.1f", elapsed_seconds)

    if verify:
        out_hr = read_record_series_fitparse(out_path, ["heart_rate", "hr"])
        logger.info("[verify] output HR samples: %d", len(out_hr.t))

    return {
        "out_path": out_path,
        "offset_s": offset_s,
        "records": rec_idx,
        "hr_written": hr_written,
        "elapsed_s": elapsed_seconds,
        "avg_power": int(round(float(np.mean(original_powers)) * power_mult)) if original_powers else None,
        "max_power": int(round(float(np.max(original_powers)) * power_mult)) if original_powers else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-fit", required=True, help="Power/base FIT")
    ap.add_argument("--hr-fit", required=True, help="Any FIT containing heart_rate")
    ap.add_argument("--out", help="Output merged FIT")
    ap.add_argument("--offset", default="auto", help="auto | none | <seconds>")
    ap.add_argument("--max-gap", type=float, default=10.0, help="Maximum allowed gap to nearest HR sample")
    ap.add_argument("--merge-temp", action="store_true")
    ap.add_argument("--power-mult", type=float, default=1.0)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--sport", default="ROWING", help="FIT sport type (e.g. ROWING, KAYAKING)")
    ap.add_argument("--sub-sport", default="VIRTUAL_ACTIVITY", help="FIT sub-sport (e.g. VIRTUAL_ACTIVITY, INDOOR_ROWING)")
    args = ap.parse_args()

    out_path = args.out or default_out_path(args.base_fit)

    merge(
        base_fit_path=args.base_fit,
        hr_fit_path=args.hr_fit,
        out_path=out_path,
        offset_arg=args.offset,
        max_gap_s=args.max_gap,
        merge_temp=args.merge_temp,
        power_mult=args.power_mult,
        verify=args.verify,
        fit_sport=args.sport,
        fit_sub_sport=args.sub_sport,
    )


if __name__ == "__main__":
    main()
