"""PROTOTYPE, 1x only: force and angle curves in a FIT file (Rowing Data Standard draft v0.1).

Not conforming, kept because it shows how curves can travel in a FIT file and
feeds the charts in intervals/force-curves. It writes GATE force into the
standard's HANDLE force fields, in a unit that is not confirmed, and it mixes
finish-to-finish power with catch-to-catch metrics. Use `rowtools convert` for
normal files.

Original notes:

Stroke-boundary recording (one Record per stroke), native fields per spec §4,
core developer fields §5.1 via the reference `rowing_data` library, and the
oarlock fields §5.2/§5.3/§5.4 (not yet implemented in that library) added with
the same application UUID and the registry's IDs, scales and units.

Per-stroke metrics come from this repository's TelemetryEngine (same segmentation and
slip/wash logic the app uses), so the comparison isolates the FIT encoding.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "analysis"))

from fit_tool.developer_field import DeveloperField  # noqa: E402
from fit_tool.fit_file_builder import FitFileBuilder  # noqa: E402
from fit_tool.profile.messages.developer_data_id_message import DeveloperDataIdMessage  # noqa: E402
from fit_tool.profile.messages.activity_message import ActivityMessage  # noqa: E402
from fit_tool.profile.messages.event_message import EventMessage  # noqa: E402
from fit_tool.profile.messages.file_id_message import FileIdMessage  # noqa: E402
from fit_tool.profile.messages.lap_message import LapMessage  # noqa: E402
from fit_tool.profile.messages.record_message import RecordMessage  # noqa: E402
from fit_tool.profile.messages.session_message import SessionMessage  # noqa: E402
from fit_tool.profile.profile_type import (  # noqa: E402
    Activity, Event, EventType, FileType, Intensity, Manufacturer, Sport, SubSport,
)
from rowing_data import writer as W  # noqa: E402
from rowing_data.constants import RecordingStrategy  # noqa: E402
from rowing_data.fields import BaseType, FieldDef, MessageType, field_by_id  # noqa: E402
from rowing_data.model import Record  # noqa: E402

from rowtools.strokes import extract_gps_from_blocks  # noqa: E402
from telemetry_engine import KGF_TO_N, TelemetryEngine  # noqa: E402

logger = logging.getLogger("peach_to_rita")

R = MessageType.RECORD


def _d(fid, name, bt, scale, units):
    return FieldDef(name=name, message=R, field_id=fid, base_type=bt, scale=scale, units=units)


# Oarlock fields from spec §5.2/§5.3 (registry/field-ids.md), not in the library yet.
OARLOCK = {
    11: _d(11, "Catch", BaseType.SINT16, 10, "deg"),
    12: _d(12, "Finish", BaseType.SINT16, 10, "deg"),
    13: _d(13, "Slip", BaseType.SINT16, 10, "deg"),
    14: _d(14, "Wash", BaseType.SINT16, 10, "deg"),
    15: _d(15, "PeakForceAngle", BaseType.SINT16, 10, "deg"),
    16: _d(16, "EffectiveArc", BaseType.UINT16, 10, "deg"),
    200: _d(200, "CatchPort", BaseType.SINT16, 10, "deg"),
    201: _d(201, "CatchStarboard", BaseType.SINT16, 10, "deg"),
    202: _d(202, "FinishPort", BaseType.SINT16, 10, "deg"),
    203: _d(203, "FinishStarboard", BaseType.SINT16, 10, "deg"),
    204: _d(204, "SlipPort", BaseType.SINT16, 10, "deg"),
    205: _d(205, "SlipStarboard", BaseType.SINT16, 10, "deg"),
    206: _d(206, "WashPort", BaseType.SINT16, 10, "deg"),
    207: _d(207, "WashStarboard", BaseType.SINT16, 10, "deg"),
    208: _d(208, "PeakForceAnglePort", BaseType.SINT16, 10, "deg"),
    209: _d(209, "PeakForceAngleStarboard", BaseType.SINT16, 10, "deg"),
    210: _d(210, "EffectiveArcPort", BaseType.UINT16, 10, "deg"),
    211: _d(211, "EffectiveArcStarboard", BaseType.UINT16, 10, "deg"),
}
# In-stroke curve (spec §6): axis metadata 90-92 and HandleForceCurve 60 under the
# RITA app ID. Per-oar force and angle curves go under this repository's own app ID, since
# the standard carries one curve only and IDs 20-89 are reserved.
CURVE_META = {
    90: _d(90, "InstrokeAbscissaType", BaseType.UINT8, 1, ""),
    91: _d(91, "InstrokeSampleInterval", BaseType.UINT16, 1, "ms"),
    92: _d(92, "InstrokePointCount", BaseType.UINT8, 1, ""),
}
HANDLE_FORCE_CURVE = _d(60, "HandleForceCurve", BaseType.UINT16, 10, "N")
OWN_APP_ID = list(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_DNS, "rowing-telemetry-tools").bytes)
CL_INDEX = 1
CL_CURVES = {  # name -> (field id, base type, scale, units)
    "ForceCurvePort": (0, BaseType.UINT16, 10, "N"),
    "ForceCurveStarboard": (1, BaseType.UINT16, 10, "N"),
    "AngleCurvePort": (2, BaseType.SINT16, 10, "deg"),
    "AngleCurveStarboard": (3, BaseType.SINT16, 10, "deg"),
}
SAMPLE_MS = 20          # Peach 50 Hz
MAX_POINTS = 127        # FIT limit for a UINT16 array (spec §6.5)
CURVE_POINTS = None     # None: raw 50 Hz samples (padded to 127); int: resample every drive to this many points

CORE_IDS = (1, 3, 6, 7, 8, 19)
CORE_ATTR = {1: "stroke_drive_time_ms", 3: "stroke_recovery_time_ms", 6: "average_drive_force",
             7: "peak_drive_force", 8: "average_boat_speed_mps", 19: "stroke_work_j"}

SEMI_PER_DEG = (1 << 31) / 180.0


def _f(x):
    x = float(x)
    return x if np.isfinite(x) else None


def _drive_force_stats(force: np.ndarray, angle: np.ndarray):
    """Peak, mean drive force (contiguous window round the peak) and angle at peak."""
    f = np.asarray(force, dtype=float)
    a = np.asarray(angle, dtype=float)
    ok = np.isfinite(f) & np.isfinite(a)
    if ok.sum() < 3:
        return None, None, None
    f, a = f[ok], a[ok]
    pk = int(np.argmax(f))
    peak = float(f[pk])
    if peak <= 0:
        return None, None, None
    thr = max(20.0, 0.05 * peak)
    lo = pk
    while lo > 0 and f[lo - 1] >= thr:
        lo -= 1
    hi = pk
    while hi < len(f) - 1 and f[hi + 1] >= thr:
        hi += 1
    return peak, float(np.mean(f[lo:hi + 1])), float(a[pk])


def _drive_window(force, angle):
    """Catch (min angle before peak force) to finish (max angle after it), in samples."""
    f = np.nan_to_num(np.asarray(force, dtype=float))
    a = np.asarray(angle, dtype=float)
    if len(f) < 5 or not np.isfinite(a).any() or f.max() <= 50:
        return None
    pk = int(np.argmax(f))
    span = 75  # 1.5 s either side of the peak
    lo = max(0, pk - span)
    hi = min(len(a), pk + span + 1)
    a_fill = np.where(np.isfinite(a), a, np.nan)
    # Catch: last sample before the peak within 0.5 deg of the minimum angle.
    # Finish: first sample after the peak within 0.5 deg of the maximum. This keeps
    # dwell plateaus at the catch and finish out of the drive window.
    pre, post = a_fill[lo:pk + 1], a_fill[pk:hi]
    c = lo + int(np.nanmax(np.where(pre <= np.nanmin(pre) + 0.5)[0]))
    fin = pk + int(np.nanmin(np.where(post >= np.nanmax(post) - 0.5)[0]))
    return (c, fin) if fin > c else None


def _stroke_curves(ch):
    """Common catch-to-finish window over both oars, raw 50 Hz samples."""
    w_p = _drive_window(ch["GateForceX_1"], ch["GateAngle_1"])
    w_s = _drive_window(ch["S_GateForceX_1"], ch["S_GateAngle_1"])
    wins = [w for w in (w_p, w_s) if w]
    if not wins:
        return None
    lo = min(w[0] for w in wins)
    hi = max(w[1] for w in wins) + 1
    # Cap the drive window at 127 raw samples (2.54 s) in both modes: longer windows
    # only occur on paddling/restart strokes where the catch-finish detection runs on.
    hi = min(hi, lo + MAX_POINTS)
    if hi - lo < 3:
        return None

    if CURVE_POINTS is not None:
        # Uniform resample to exactly CURVE_POINTS points. The spec's sample interval
        # is whole ms, so round the step and let the window end move by < step/2 x (n-1)
        # rounding (at most ~half a step per point, typically 1-2 raw samples in total).
        n = CURVE_POINTS
        t_src = np.arange(len(ch["GateForceX_1"]), dtype=float) * SAMPLE_MS
        step = max(1, int(round((hi - 1 - lo) * SAMPLE_MS / (n - 1))))
        t_new = lo * SAMPLE_MS + np.arange(n) * step

        def _interp(key):
            y = np.asarray(ch[key], dtype=float)
            m = np.isfinite(y)
            return np.interp(t_new, t_src[m], y[m]) if m.sum() >= 2 else np.zeros(n)

        fp = np.clip(_interp("GateForceX_1"), 0, 6553.4)
        fs = np.clip(_interp("S_GateForceX_1"), 0, 6553.4)
        return {
            "HandleForceCurve": (fp + fs) / 2.0,
            "ForceCurvePort": fp, "ForceCurveStarboard": fs,
            "AngleCurvePort": np.clip(_interp("GateAngle_1"), -3276.6, 3276.6),
            "AngleCurveStarboard": np.clip(_interp("S_GateAngle_1"), -3276.6, 3276.6),
            "_interval": step,
        }

    def force(key):
        x = np.asarray(ch[key][lo:hi], dtype=float)
        return np.clip(np.nan_to_num(x), 0, 6553.4)  # negative -> 0 (§6.5), stay below invalid

    def angle(key):
        x = np.asarray(ch[key][lo:hi], dtype=float)
        return np.clip(np.nan_to_num(x), -3276.6, 3276.6)

    fp, fs = force("GateForceX_1"), force("S_GateForceX_1")
    return {
        "HandleForceCurve": (fp + fs) / 2.0,
        "ForceCurvePort": fp, "ForceCurveStarboard": fs,
        "AngleCurvePort": angle("GateAngle_1"), "AngleCurveStarboard": angle("S_GateAngle_1"),
        "_interval": SAMPLE_MS,
    }


_INVALID_RAW = {BaseType.UINT16: 65535, BaseType.SINT16: 32767}


def _array_field(index, field_id, name, bt, scale, units, values):
    """Fixed-size MAX_POINTS array; tail padded with the FIT invalid value (spec §6.5:
    missing sample). A fixed size keeps one message definition: fit_tool otherwise
    reuses a larger earlier definition and silently zero-pads shorter arrays."""
    fit_bt = W._BASE_TYPE_TO_FIT[bt]
    size = CURVE_POINTS or MAX_POINTS
    dev = DeveloperField(developer_data_index=index, field_id=field_id,
                         size=W._BASE_TYPE_SIZE[bt] * size, name=name,
                         base_type=fit_bt, scale=scale, offset=0, units=units)
    pad = _INVALID_RAW[bt] / scale
    for i in range(size):
        dev.set_value(i, float(values[i]) if i < len(values) else pad)
    return dev


def _mean(*vals):
    v = [x for x in vals if x is not None]
    return float(np.mean(v)) if v else None


ZERO_GAP_S = 10.0       # stroke-to-stroke gap that counts as a stop
ZERO_FORCE_N = 20.0     # any gate force above this in a second = blade loaded, not stopped


def _nonneg_mean(a):
    a = np.asarray(a, dtype=float)
    a = a[np.isfinite(a)]
    return max(0.0, float(a.mean())) if a.size else None


def _zero_fill(out, full, start_utc, anchor_ms, gt, glat, glon):
    """Insert 1 Hz zero-power records during stops (measured zero, spec §7.2).

    Only native fields: power 0, cadence 0, measured speed, distance, GPS and
    total_cycles repeated. No stroke developer fields, as there is no stroke.
    """
    t50 = full["Time"]
    spd50, dist50 = full["Speed_Boat"], full["Distance_Boat"]
    fcols = [k for k in full if "GateForceX" in k]
    fmax = np.nanmax(np.vstack([np.abs(full[k]) for k in fcols]), axis=0) if fcols else np.zeros(len(t50))
    to_peach = lambda rec: (rec.timestamp - start_utc).total_seconds() * 1000 + anchor_ms  # noqa: E731

    filled, n_zero = [], 0
    prev_t, prev_cycles = anchor_ms - 1000.0, 0
    for core, oar in out:
        t = to_peach(core)
        if t - prev_t > ZERO_GAP_S * 1000:
            for tz in np.arange(prev_t + 1000, t - 999, 1000.0):
                lo, hi = np.searchsorted(t50, [tz - 500, tz + 500])
                if hi <= lo or np.nanmax(fmax[lo:hi]) > ZERO_FORCE_N:
                    continue
                k = min(lo, len(t50) - 1)
                lat = lon = None
                if len(gt):
                    j = int(np.clip(np.searchsorted(gt, tz), 1, len(gt) - 1))
                    j = j if abs(gt[j] - tz) < abs(gt[j - 1] - tz) else j - 1
                    if abs(gt[j] - tz) <= 5000:
                        lat, lon = int(round(glat[j] * SEMI_PER_DEG)), int(round(glon[j] * SEMI_PER_DEG))
                filled.append((Record(
                    timestamp=start_utc + timedelta(milliseconds=tz - anchor_ms),
                    distance_m=max(0.0, float(dist50[k])),
                    cadence=0, cadence256=0.0, power=0,
                    enhanced_speed_mps=_nonneg_mean(spd50[lo:hi]),
                    position_lat=lat, position_long=lon,
                    total_cycles=prev_cycles or None,
                ), {}))
                n_zero += 1
        filled.append((core, oar))
        prev_t, prev_cycles = t, core.total_cycles
    logger.warning("zero-fill: inserted %d zero records", n_zero)
    return filled


def build_strokes(peach: Path, zero_fill: bool = False, curves: bool = False):
    eng = TelemetryEngine(str(peach), boat_type="1x")
    t0, t1 = eng.time_range_ms
    piece = eng.analyze_piece(t0, t1)
    if not piece.is_sculling or piece.crew_size != 1:
        raise SystemExit(f"expected 1x sculling, got crew={piece.crew_size} sculling={piece.is_sculling}")
    p, seat = piece.periodic, 1
    start_utc = eng.start_utc
    # Peach session time (ms) of the first periodic row maps to start_utc in the
    # current converter; use the same anchor so both files line up in Intervals.
    anchor_ms = float(eng._peach_rows[0]["time"])

    full = eng.reader.to_dict(skip_inactive=False, compute_derived=True, include_unknown=False)
    t50, dist50 = full["Time"], full["Distance_Boat"]

    gps = extract_gps_from_blocks(eng.reader.data)
    gt = np.array([g["time_ms"] for g in gps], dtype=float)
    o = np.argsort(gt)
    gt = gt[o]
    glat = np.array([g["lat_deg"] for g in gps])[o]
    glon = np.array([g["lon_deg"] for g in gps])[o]

    out = []
    for i, s in enumerate(piece.strokes):
        ch = s.channels
        pk_p, av_p, ang_p = _drive_force_stats(ch["GateForceX_1"], ch["GateAngle_1"])
        pk_s, av_s, ang_s = _drive_force_stats(ch["S_GateForceX_1"], ch["S_GateAngle_1"])
        g = lambda d: _f(d[seat][i]) if seat in d else None  # noqa: E731
        catch_p, catch_s = g(p.catch_angle), g(p.catch_angle_stbd)
        fin_p, fin_s = g(p.finish_angle), g(p.finish_angle_stbd)
        slip_p, slip_s = g(p.slip_angle), g(p.slip_angle_stbd)
        wash_p, wash_s = g(p.wash_angle), g(p.wash_angle_stbd)
        ea_p, ea_s = g(p.effective_arc), g(p.effective_arc_stbd)
        rate = _f(p.rate_spm[i])
        spd = _f(p.avg_speed[i])
        dur_s = float(p.duration_ms[i]) / 1000.0

        # Records are timestamped at stroke end (stroke-boundary, completed stroke).
        t_end = float(s.time_end_ms)
        k = int(np.clip(np.searchsorted(t50, t_end), 0, len(t50) - 1))
        dist = _f(dist50[k])
        lat = lon = None
        if len(gt):
            j = int(np.clip(np.searchsorted(gt, t_end), 1, len(gt) - 1))
            j = j if abs(gt[j] - t_end) < abs(gt[j - 1] - t_end) else j - 1
            if abs(gt[j] - t_end) <= 5000:
                lat, lon = int(round(glat[j] * SEMI_PER_DEG)), int(round(glon[j] * SEMI_PER_DEG))

        power = _f(p.swivel_power[seat][i])
        work = _f(p.work_j[seat][i])
        drive = _f(p.drive_time_ms[seat][i])
        rec_t = _f(p.recovery_time_ms[seat][i])
        # Strokes spanning a stop (drive > 3 s or cycle > 15 s) have no meaningful
        # phase timing; omit rather than write implausible values (spec §7.2).
        if (drive is not None and drive > 3000) or dur_s > 15:
            drive = rec_t = None
        core = Record(
            timestamp=start_utc + timedelta(milliseconds=t_end - anchor_ms),
            distance_m=max(0.0, dist) if dist is not None else None,
            stroke_rate=rate if rate and rate > 0 else None,
            power=int(round(power)) if power is not None and power >= 0 else None,
            enhanced_speed_mps=max(0.0, spd) if spd is not None else None,
            position_lat=lat, position_long=lon,
            total_cycles=i + 1,
            cycle_length_m=(spd * dur_s) if spd is not None and spd >= 0 else None,
            stroke_drive_time_ms=int(round(drive)) if drive is not None and drive >= 0 else None,
            stroke_recovery_time_ms=int(round(rec_t)) if rec_t is not None and rec_t >= 0 else None,
            average_drive_force=_mean(av_p, av_s),
            peak_drive_force=_mean(pk_p, pk_s),
            average_boat_speed_mps=max(0.0, spd) if spd is not None else None,
            stroke_work_j=int(round(work)) if work is not None and work >= 0 else None,
        )
        oar = {
            11: _mean(catch_p, catch_s), 12: _mean(fin_p, fin_s),
            13: _mean(slip_p, slip_s), 14: _mean(wash_p, wash_s),
            15: _mean(ang_p, ang_s), 16: _mean(ea_p, ea_s),
            200: catch_p, 201: catch_s, 202: fin_p, 203: fin_s,
            204: slip_p, 205: slip_s, 206: wash_p, 207: wash_s,
            208: ang_p, 209: ang_s, 210: ea_p, 211: ea_s,
        }
        oar = {fid: v for fid, v in oar.items()
               if v is not None and not (OARLOCK[fid].base_type == BaseType.UINT16 and v < 0)}
        if curves:
            cv = _stroke_curves(ch)
            if cv:
                oar["_curves"] = cv
        out.append((core, oar))

    if zero_fill:
        out = _zero_fill(out, full, start_utc, anchor_ms, gt, glat, glon)

    thresholds = (int(round(eng.sculling_slip_threshold_kgf * KGF_TO_N)),
                  int(round(eng.sculling_wash_threshold_kgf * KGF_TO_N)))
    return out, start_utc, thresholds


def _dev(field: FieldDef, value):
    # clip measured values that would encode to the FIT invalid value (§7.2)
    raw = round(value * field.scale)
    if field.base_type == BaseType.SINT16 and raw >= 32767:
        value = 32766 / field.scale
    if field.base_type == BaseType.UINT16 and raw >= 65535:
        value = 65534 / field.scale
    return W._developer_field(field, value)


def write(strokes, start_utc, thresholds, out: Path):
    start_ms = W._unix_ms(start_utc)
    last_ms = W._unix_ms(strokes[-1][0].timestamp)
    elapsed = (last_ms - start_ms) / 1000.0
    total_dist = next((c.distance_m for c, _ in reversed(strokes) if c.distance_m is not None), 0.0)
    core_fields = [field_by_id(f) for f in CORE_IDS]
    oar_ids = sorted({fid for _, o in strokes for fid in o if isinstance(fid, int)})
    has_curves = any("_curves" in o for _, o in strokes)
    slip_f, wash_f = field_by_id(94), field_by_id(95)

    b = FitFileBuilder(auto_define=True, min_string_size=64)
    fid = FileIdMessage()
    fid.type = FileType.ACTIVITY
    fid.manufacturer = Manufacturer.DEVELOPMENT
    fid.product = 0
    fid.serial_number = 0
    fid.time_created = start_ms
    b.add(fid)

    ev = EventMessage()
    ev.event, ev.event_type, ev.timestamp = Event.TIMER, EventType.START, start_ms
    b.add(ev)

    W._add_developer_definitions(b, tuple(core_fields), ((slip_f, thresholds[0]), (wash_f, thresholds[1])))
    for f in oar_ids:
        b.add(W._field_description(OARLOCK[f]))
    if has_curves:
        for f in (*CURVE_META.values(), HANDLE_FORCE_CURVE):
            msg = W._field_description(f)
            if f is HANDLE_FORCE_CURVE:
                msg.array = 1
            b.add(msg)
        cl = DeveloperDataIdMessage()
        cl.application_id = OWN_APP_ID
        cl.application_version = 1
        cl.developer_data_index = CL_INDEX
        b.add(cl)
        for name, (fid_, bt, sc, un) in CL_CURVES.items():
            msg = W._field_description(FieldDef(name=name, message=R, field_id=fid_,
                                                base_type=bt, scale=sc, units=un))
            msg.developer_data_index = CL_INDEX
            msg.array = 1
            b.add(msg)

    for core, oar in strokes:
        dev = [_dev(f, getattr(core, CORE_ATTR[f.field_id])) for f in core_fields
               if getattr(core, CORE_ATTR[f.field_id]) is not None]
        dev += [_dev(OARLOCK[f], v) for f, v in sorted((k, v) for k, v in oar.items() if isinstance(k, int))]
        cv = oar.get("_curves")
        if cv is not None:
            n = len(cv["HandleForceCurve"])
            dev += [_dev(CURVE_META[90], 1), _dev(CURVE_META[91], cv["_interval"]), _dev(CURVE_META[92], n)]
            h = HANDLE_FORCE_CURVE
            dev.append(_array_field(0, 60, h.name, h.base_type, h.scale, h.units, cv["HandleForceCurve"]))
            for name, (fid_, bt, sc, un) in CL_CURVES.items():
                dev.append(_array_field(CL_INDEX, fid_, name, bt, sc, un, cv[name]))
        rec = RecordMessage(developer_fields=dev)
        rec.timestamp = W._unix_ms(core.timestamp)
        W._set_native_fields(rec, core)
        cad, cad256 = W._native_cadence(core)
        if cad is not None:
            rec.cadence = cad
            rec.cadence256 = cad256
        b.add(rec)

    lap = LapMessage()
    lap.message_index = 0
    lap.timestamp = last_ms
    lap.start_time = start_ms
    lap.total_elapsed_time = lap.total_timer_time = elapsed
    lap.total_distance = total_dist
    lap.sport = Sport.ROWING
    lap.intensity = Intensity.ACTIVE
    b.add(lap)

    sess = SessionMessage(developer_fields=[
        W._developer_field(field_by_id(10), int(RecordingStrategy.STROKE_BOUNDARY)),
        W._developer_field(slip_f, thresholds[0]),
        W._developer_field(wash_f, thresholds[1]),
    ])
    sess.message_index = 0
    sess.timestamp = last_ms
    sess.start_time = start_ms
    sess.total_elapsed_time = sess.total_timer_time = elapsed
    sess.total_distance = total_dist
    sess.sport = Sport.ROWING
    sess.sub_sport = SubSport.GENERIC
    b.add(sess)

    act = ActivityMessage()
    act.timestamp = last_ms
    act.total_timer_time = elapsed
    act.num_sessions = 1
    act.type = Activity.MANUAL
    b.add(act)

    ev2 = EventMessage()
    ev2.event, ev2.event_type, ev2.timestamp = Event.TIMER, EventType.STOP_ALL, last_ms
    b.add(ev2)

    b.build().to_file(str(out))
    return len(strokes), oar_ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("peach")
    ap.add_argument("out")
    ap.add_argument("--zero-fill", action="store_true",
                    help="write 1 Hz power=0/cadence=0 records during stops")
    ap.add_argument("--curves", action="store_true",
                    help="add HandleForceCurve (RITA 60) and per-oar force/angle curves (the original app app ID)")
    ap.add_argument("--points", type=int, default=None,
                    help="resample each drive to exactly N curve points (2-127); default raw 50 Hz")
    a = ap.parse_args()
    if a.points is not None:
        if not 2 <= a.points <= MAX_POINTS:
            raise SystemExit("--points must be between 2 and 127")
        global CURVE_POINTS
        CURVE_POINTS = a.points
    logging.basicConfig(level=logging.WARNING)
    strokes, start, thr = build_strokes(Path(a.peach), zero_fill=a.zero_fill, curves=a.curves)
    n, ids = write(strokes, start, thr, Path(a.out))
    print(f"wrote {a.out}: {n} records, oarlock ids {ids}, thresholds {thr} N")


if __name__ == "__main__":
    main()
