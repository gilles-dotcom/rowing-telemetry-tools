"""Write one FIT file per seat, one record per stroke.

Follows the field mappings of the Rowing Data Standard 1.0-RC (commit a6225409,
https://github.com/Rowing-Industry-Trade-Association/rowing-data-standard) for
native fields. See docs/rita-mapping.md for what is exact, open or left out.

Two modes for stops (gaps longer than 10 s without a stroke):

* ``zero``  one 0 W record per second. Needed by training platforms, which
  otherwise draw power through the stop. Records that are not strokes are not
  described by the standard, so this file does not claim a recording strategy.
* ``gap``   nothing is written. RecordingStrategy = StrokeBoundary.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from fit_tool.base_type import BaseType
from fit_tool.developer_field import DeveloperField
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.developer_data_id_message import DeveloperDataIdMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.field_description_message import FieldDescriptionMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import (Activity, Event, EventType, FileType, Intensity,
                                           Manufacturer, Sport, SubSport)

from .strokes import StrokeSession, classify_power, cycle_span_ms

logger = logging.getLogger(__name__)

RITA_APP_ID = bytes.fromhex("89e861586d475c989d467d29437f27b9")
RITA_PROTOCOL_VERSION = 1
RECORDING_STRATEGY_FIELD = 10
STROKE_BOUNDARY = 1
STOP_GAP_MS = 10_000
GPS_MAX_GAP_MS = 5_000


@dataclass
class Rec:
    t_ms: float
    power: float | None
    rate: float | None
    speed: float | None
    distance: float | None
    stroke_m: float | None
    cycles: int
    lat: float | None = None
    lon: float | None = None
    hr: int | None = None
    is_stroke: bool = True


def resolve_start(ses: StrokeSession, start_utc: datetime | None, utc_offset_min: int | None) -> datetime:
    """UTC at session time 0.

    The file header holds the logger's own clock at session time 0, normally
    LOCAL time (checked against GPS time on 15 files). It is not UTC and it is
    not the time of the first stroke.
    """
    if start_utc is not None:
        return start_utc if start_utc.tzinfo else start_utc.replace(tzinfo=timezone.utc)
    if ses.header_time is None:
        raise ValueError("no usable clock in the file: pass a start time in UTC")
    if utc_offset_min is None:
        raise ValueError("the logger clock is local time: pass its UTC offset in minutes")
    return ses.header_time - timedelta(minutes=utc_offset_min)


def build_records(ses: StrokeSession, seat: int, stops: str = "zero") -> list[Rec]:
    if stops not in ("zero", "gap"):
        raise ValueError("stops must be 'zero' or 'gap'")
    power, _ = classify_power(ses, seat)
    span = cycle_span_ms(ses)
    cum = ses.cumulative_m
    t = ses.time_ms
    out: list[Rec] = []
    for i in range(len(t)):
        if i and stops == "zero" and t[i] - t[i - 1] > STOP_GAP_MS:
            k = t[i - 1] + 1000.0
            while k < t[i] - span[i]:
                dist = None if cum is None else float(_distance_at(ses, k))
                out.append(Rec(k, 0.0, 0.0, _speed_at(ses, k), dist, None, i, is_stroke=False))
                k += 1000.0
        p = power[i]
        out.append(Rec(float(t[i]), None if math.isnan(p) else float(p), float(ses.rate_spm[i]) or None,
                       float(ses.speed_mps[i]), None if cum is None else float(cum[i]),
                       float(ses.distance_m[i]) or None, i + 1))
    _add_gps(ses, out)
    return out


def _distance_at(ses: StrokeSession, t_ms: float) -> float:
    return np.interp(t_ms, ses.time_ms, ses.cumulative_m)


def _speed_at(ses: StrokeSession, t_ms: float) -> float | None:
    d = ses.samples or {}
    if "Speed_Boat" not in d:
        return None
    t = d["Time"].astype(float)
    a, b = np.searchsorted(t, [t_ms - 500, t_ms + 500])
    v = d["Speed_Boat"][a:b].astype(float)
    v = v[np.isfinite(v)]
    return float(max(0.0, v.mean())) if len(v) else None


def _add_gps(ses: StrokeSession, recs: list[Rec]) -> None:
    if not ses.gps:
        return
    g = sorted(ses.gps, key=lambda p: p["time_ms"])
    gt = np.array([p["time_ms"] for p in g], float)
    for r in recs:
        j = int(np.clip(np.searchsorted(gt, r.t_ms), 1, len(gt) - 1)) if len(gt) > 1 else 0
        if j and abs(gt[j - 1] - r.t_ms) < abs(gt[j] - r.t_ms):
            j -= 1
        if abs(gt[j] - r.t_ms) <= GPS_MAX_GAP_MS:
            r.lat, r.lon = g[j]["lat_deg"], g[j]["lon_deg"]


def add_heart_rate(recs: list[Rec], t0: datetime, hr_fit: str | Path, offset_s: float = 0.0,
                   max_gap_s: float = 5.0) -> int:
    """Nearest heart rate within ``max_gap_s``, never held longer. A positive
    offset moves the heart rate LATER. Returns the number of records filled."""
    import fitparse

    ts, hr = [], []
    for m in fitparse.FitFile(str(hr_fit)).get_messages("record"):
        v = m.get_values()
        if v.get("heart_rate") is not None and v.get("timestamp") is not None:
            ts.append(v["timestamp"].replace(tzinfo=timezone.utc).timestamp() + offset_s)
            hr.append(int(v["heart_rate"]))
    if not ts:
        raise ValueError(f"no heart rate in {Path(hr_fit).name}")
    ts_a = np.array(ts)
    o = np.argsort(ts_a)
    ts_a, hr_a = ts_a[o], np.array(hr)[o]
    base, n = t0.timestamp(), 0
    for r in recs:
        x = base + r.t_ms / 1000.0
        j = int(np.clip(np.searchsorted(ts_a, x), 0, len(ts_a) - 1))
        if j and abs(ts_a[j - 1] - x) < abs(ts_a[j] - x):
            j -= 1
        if abs(ts_a[j] - x) <= max_gap_s:
            r.hr = int(hr_a[j])
            n += 1
    return n


SPORTS = {"rowing": Sport.ROWING, "kayaking": Sport.KAYAKING}


def write_fit(recs: list[Rec], t0: datetime, out: str | Path, stops: str = "zero",
              include_gps: bool = True, sport: str = "rowing") -> Path:
    """sport: "rowing" or "kayaking". Use a different one for sculling than for sweep so a
    platform keeps two power curves and two sets of zones (see docs/sweep-and-sculling.md)."""
    if not recs:
        raise ValueError("nothing to write")
    def ms(t: float) -> int:
        # FIT has 1 s resolution. Not round(): half-to-even makes x.5 s collide in pairs.
        return math.floor((t0.timestamp() * 1000 + t) / 1000.0 + 0.5) * 1000

    b = FitFileBuilder(auto_define=True, min_string_size=50)
    fid = FileIdMessage()
    fid.type = FileType.ACTIVITY
    fid.manufacturer = Manufacturer.DEVELOPMENT.value
    fid.product = 0
    fid.serial_number = 0
    fid.time_created = ms(recs[0].t_ms)
    b.add(fid)
    strategy = stops == "gap"
    if strategy:
        dd = DeveloperDataIdMessage()
        dd.application_id = list(RITA_APP_ID)
        dd.application_version = RITA_PROTOCOL_VERSION
        dd.developer_data_index = 0
        b.add(dd)
        fd = FieldDescriptionMessage()
        fd.developer_data_index = 0
        fd.field_definition_number = RECORDING_STRATEGY_FIELD
        fd.fit_base_type_id = BaseType.UINT8.value
        fd.field_name = "RecordingStrategy"
        fd.units = ""
        b.add(fd)
    ev = EventMessage()
    ev.timestamp, ev.event, ev.event_type = ms(recs[0].t_ms), Event.TIMER, EventType.START
    b.add(ev)

    last = None
    written: list[Rec] = []
    for r in recs:
        stamp = ms(r.t_ms)
        if last is not None and stamp <= last:
            if not r.is_stroke:
                continue
            stamp = last + 1000          # keep every stroke, time stamps must increase
        last = stamp
        m = RecordMessage()
        m.timestamp = stamp
        if r.power is not None:
            m.power = int(round(r.power))
        if r.rate is not None:
            m.cadence = int(round(r.rate))
            if r.is_stroke:
                m.cadence256 = r.rate
        if r.speed is not None:
            m.enhanced_speed = r.speed
        if r.distance is not None:
            m.distance = r.distance
        m.total_cycles = r.cycles
        if r.hr is not None:
            m.heart_rate = r.hr
        if include_gps and r.lat is not None:
            m.position_lat, m.position_long = r.lat, r.lon
        b.add(m)
        written.append(r)
    end = last
    ev = EventMessage()
    ev.timestamp, ev.event, ev.event_type = end, Event.TIMER, EventType.STOP_ALL
    b.add(ev)

    elapsed = (end - ms(recs[0].t_ms)) / 1000.0
    strokes = [r for r in written if r.is_stroke]
    pw = [r.power for r in strokes if r.power is not None]
    hrs = [r.hr for r in written if r.hr is not None]
    dist = next((r.distance for r in reversed(written) if r.distance is not None), None)
    dev = []
    if strategy:
        f = DeveloperField(developer_data_index=0, field_id=RECORDING_STRATEGY_FIELD, size=1,
                           base_type=BaseType.UINT8, name="RecordingStrategy")
        f.set_value(0, STROKE_BOUNDARY)
        dev = [f]
    for cls in (LapMessage, SessionMessage):
        m = SessionMessage(developer_fields=dev) if cls is SessionMessage and dev else cls()
        m.timestamp, m.start_time = end, ms(recs[0].t_ms)
        m.total_elapsed_time = m.total_timer_time = elapsed
        m.sport, m.sub_sport = SPORTS[sport], SubSport.GENERIC
        m.total_cycles = len(strokes)
        if dist is not None:
            m.total_distance = dist
        if pw:
            m.max_power = int(round(max(pw)))
        if hrs:
            m.avg_heart_rate, m.max_heart_rate = int(round(np.mean(hrs))), max(hrs)
        if cls is LapMessage:
            m.intensity = Intensity.ACTIVE
        else:
            m.first_lap_index, m.num_laps = 0, 1
        b.add(m)
    a = ActivityMessage()
    a.timestamp, a.total_timer_time, a.num_sessions = end, elapsed, 1
    a.type, a.event, a.event_type = Activity.MANUAL, Event.ACTIVITY, EventType.STOP
    b.add(a)
    out = Path(out)
    b.build().to_file(str(out))
    return out
