"""Per-stroke rows, boat layout, start time and GPS from a ``.peach-data`` file.

The low-level scanning here is the original reverse-engineered code, unchanged
in behaviour. What is new is how power is read: the file stores power as
``watts + 1001`` and uses raw ``0`` for "no value". Those are different things,
so a missing value is returned as ``None`` and never as 0 W or as the previous
stroke's value.
"""
from __future__ import annotations

import logging
import math
import struct
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .samples import PeachBinaryReader

logger = logging.getLogger(__name__)

# Byte offsets relative to a stroke row's u32 time stamp.
SWEEP_STROKE_REL = 8
RATING_REL = 10
SPEED_REL = 12
DIST_REL = 16
PAIR_POWER_REL = 34
PAIR_STRIDE = 2
SWEEP_POWER_REL = 22
SWEEP_STRIDE = 14
PAIR_SEAT1_CURRENT_REL = SWEEP_POWER_REL
# Names kept from the original code. SCULL_RIGHT_REL holds PORT power and
# SCULL_LEFT_REL holds STARBOARD power (checked against PowerLine Excel exports).
SCULL_RIGHT_REL = 22
SCULL_LEFT_REL = 24
SCULL_TOTAL_REL = 46
SCULL_SEAT_STRIDE = 26
POWER_OFFSET = 1001

SCULL_SIDE_BAND_BOATS = {"1x", "2x"}
SCULL_SIDE_BAND_MAX_POWER_W = 2000
SWEEP_SIDE_BAND_MAX_POWER_W = 1200

DT_MIN = 100
DT_MAX = 10000


class FastReader:
    def __init__(self, data: bytes):
        self.data = data
        self.mv = memoryview(data)

    def u32(self, pos: int) -> int:
        return struct.unpack_from("<I", self.mv, pos)[0]

    def u16(self, pos: int) -> int:
        return struct.unpack_from("<H", self.mv, pos)[0]

    def u8(self, pos: int) -> int:
        return self.data[pos]


class Run:
    __slots__ = ("time_pos", "stride", "run_len", "first_time", "last_time")

    def __init__(self, time_pos: int, stride: int, run_len: int, first_time: int, last_time: int):
        self.time_pos = time_pos
        self.stride = stride
        self.run_len = run_len
        self.first_time = first_time
        self.last_time = last_time

    @property
    def duration(self) -> int:
        return self.last_time - self.first_time


def parse_utc(s: Any) -> datetime:
    if isinstance(s, datetime):
        if s.tzinfo is None:
            return s.replace(tzinfo=timezone.utc)
        return s.astimezone(timezone.utc)
    if isinstance(s, date):
        return datetime.combine(s, datetime.min.time(), tzinfo=timezone.utc)
    s = str(s).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def extract_start_utc_from_peach_data(peach_path: Path) -> datetime | None:
    data = peach_path.read_bytes()
    if len(data) < 28:
        raise ValueError(f"{peach_path} too small to contain Peach start time")

    raw = struct.unpack_from("<I", data, 24)[0]
    start_utc = datetime.fromtimestamp(raw, timezone.utc)
    if 2005 <= start_utc.year <= 2040:
        return start_utc
    return None


def resolve_start_utc(peach_path: Path, explicit_start: str | None) -> datetime:
    if explicit_start:
        return parse_utc(explicit_start)
    start_utc = extract_start_utc_from_peach_data(peach_path)
    if start_utc is not None:
        return start_utc
    logger.warning("Invalid Peach header timestamp in %s; falling back to file mtime", peach_path)
    return datetime.fromtimestamp(peach_path.stat().st_mtime, timezone.utc)


def row_sanity(fr: FastReader, pos: int, loose: bool = False) -> bool:
    try:
        rating = 0.5 * fr.u8(pos + RATING_REL) - 0.5
        speed = (fr.u16(pos + SPEED_REL) / 256.0) - (1.0 / 256.0)
        dist = (fr.u16(pos + DIST_REL) / 256.0) - (1.0 / 256.0)
    except Exception:
        return False

    if loose:
        return (0 <= rating <= 70) and (0 <= speed <= 15) and (0 <= dist <= 50)
    return (0 <= rating <= 60) and (0 <= speed <= 12) and (0 <= dist <= 35)


def find_runs(fr: FastReader, n: int, step: int, min_run: int, strides: list[int], loose: bool = False):
    runs = []
    u32 = fr.u32
    sanity = lambda pos: row_sanity(fr, pos, loose=loose)
    word_len = n // 4
    u32_words = np.frombuffer(fr.data[:word_len * 4], dtype="<u4")

    def candidate_positions(stride: int, max_pos: int):
        if step != 4 or stride % 4 != 0 or max_pos <= 0:
            return range(0, max_pos, step)
        stride_words = stride // 4
        max_idx = (max_pos + 3) // 4
        if max_idx <= 0 or stride_words >= len(u32_words):
            return ()
        idx = np.arange(max_idx, dtype=np.int64)
        valid = idx + stride_words < len(u32_words)
        idx = idx[valid]
        if len(idx) == 0:
            return ()
        t0 = u32_words[idx].astype(np.int64)
        t1 = u32_words[idx + stride_words].astype(np.int64)
        dt = t1 - t0
        keep = (dt >= DT_MIN) & (dt <= DT_MAX)
        return (idx[keep] * 4).tolist()

    for stride in strides:
        max_pos = n - 4 - stride * (min_run + 1)
        for pos in candidate_positions(stride, max_pos):
            try:
                t0 = u32(pos)
                t1 = u32(pos + stride)
            except Exception:
                continue

            dt = t1 - t0
            if dt < DT_MIN or dt > DT_MAX:
                continue
            if not sanity(pos):
                continue

            prev = t1
            ok = 1
            p = pos + 2 * stride
            while p + 4 <= n and ok < min_run + 300:
                t = u32(p)
                dt = t - prev
                if dt < DT_MIN or dt > DT_MAX:
                    break
                ok += 1
                prev = t
                p += stride

            if ok >= min_run:
                runs.append(Run(pos, stride, ok + 1, t0, prev))

    runs.sort(key=lambda r: (r.run_len, r.duration, r.time_pos), reverse=True)
    return runs


def build_time_map_general(fr: FastReader, runs, max_runs: int = 180, loose: bool = False):
    time_map = {}
    if not runs:
        return time_map

    best_len = runs[0].run_len
    best_dur = runs[0].duration
    kept = []

    for r in runs:
        if r.run_len < max(5, best_len - 50):
            continue
        if r.duration < max(500, int(best_dur * 0.05)):
            continue
        kept.append(r)
        if len(kept) >= max_runs:
            break

    for r in kept:
        prev = None
        pos = r.time_pos
        for _ in range(r.run_len):
            try:
                t = fr.u32(pos)
            except Exception:
                break
            if prev is not None:
                dt = t - prev
                if dt < DT_MIN or dt > DT_MAX:
                    break
            prev = t
            if row_sanity(fr, pos, loose=loose):
                time_map[t] = pos
            pos += r.stride

    return dict(sorted(time_map.items()))


def build_time_map_scull(fr: FastReader, runs, max_runs: int = 300):
    time_map = {}
    if not runs:
        return time_map

    best = runs[0]
    best_len = best.run_len
    best_dur = best.duration
    best_stride = best.stride

    kept = []
    for r in runs:
        if r.stride != best_stride:
            continue
        if r.run_len < max(6, best_len - 5):
            continue
        if r.duration < max(600, int(best_dur * 0.10)):
            continue
        kept.append(r)
        if len(kept) >= max_runs:
            break

    for r in kept:
        prev = None
        pos = r.time_pos
        for _ in range(r.run_len):
            try:
                t = fr.u32(pos)
            except Exception:
                break
            if prev is not None:
                dt = t - prev
                if dt < DT_MIN or dt > DT_MAX:
                    break
            prev = t
            if row_sanity(fr, pos, loose=True) and t not in time_map:
                time_map[t] = pos
            pos += r.stride

    return dict(sorted(time_map.items()))


def seat_stats_sweep(fr: FastReader, time_map, crew: int, sample_limit: int = 500):
    stats = []
    items = list(time_map.items())[:sample_limit]
    for seat in range(crew):
        vals = []
        base = SWEEP_POWER_REL + seat * SWEEP_STRIDE
        for _, pos in items:
            try:
                p = fr.u16(pos + base) - 1001
            except Exception:
                p = 0
            vals.append(max(0.0, float(p)))
        if not vals:
            stats.append((0.0, 0.0, 0.0))
            continue
        mean_v = sum(vals) / len(vals)
        nonzero_frac = sum(1 for v in vals if v > 0) / len(vals)
        var = sum((v - mean_v) ** 2 for v in vals) / len(vals)
        stats.append((mean_v, var ** 0.5, nonzero_frac))
    return stats


def is_active(stat):
    mean_v, std_v, nz = stat
    return nz > 0.15 and std_v > 3 and 5 < mean_v < 2000


def detect_layout(fr: FastReader, data: bytes, user_boat: str):
    n = len(data)

    if user_boat in {"1x", "2x", "4x"}:
        strides = {"1x": [48], "2x": [80], "4x": [144]}[user_boat]
        runs = find_runs(fr, n, 4, 8, strides, loose=True)
        time_map = build_time_map_scull(fr, runs, max_runs=300)
        crew = {"1x": 1, "2x": 2, "4x": 4}[user_boat]
        return {"family": "scull", "boat": user_boat, "crew": crew, "runs": runs, "time_map": time_map}

    if user_boat in {"2", "4", "8"}:
        stride_map = {"2": [56], "4": [80], "8": [136]}
        crew = int(user_boat)
        runs = find_runs(fr, n, 4, 8, stride_map[user_boat], loose=False)
        time_map = build_time_map_general(fr, runs, max_runs=500, loose=False)
        if user_boat == "8" and len(time_map) < 50:
            # stride-136 failed, try stride-120 (8+ with 1 disconnected sensor)
            runs_120 = find_runs(fr, n, 4, 8, [120], loose=False)
            tm_120 = build_time_map_general(fr, runs_120, max_runs=500, loose=False)
            if len(tm_120) > 50:
                logger.info('detect_layout: stride-120 detected for user_boat=8 (7 active sensors)')
                return {"family": "sweep", "boat": "8", "crew": 8, "active_slots": 7,
                        "runs": runs_120, "time_map": tm_120}
        return {"family": "sweep", "boat": user_boat, "crew": crew, "runs": runs, "time_map": time_map}

    runs_48 = find_runs(fr, n, 4, 8, [48], loose=True)
    if runs_48:
        tm_48 = build_time_map_scull(fr, runs_48, max_runs=300)
        if len(tm_48) > 200:
            return {"family": "scull", "boat": "1x", "crew": 1, "runs": runs_48, "time_map": tm_48}

    runs_144 = find_runs(fr, n, 4, 8, [144], loose=True)
    if runs_144:
        tm_144 = build_time_map_scull(fr, runs_144, max_runs=300)
        if len(tm_144) > 200:
            return {"family": "scull", "boat": "4x", "crew": 4, "runs": runs_144, "time_map": tm_144}

    runs_56 = find_runs(fr, n, 4, 8, [56], loose=False)
    if runs_56:
        tm_56 = build_time_map_general(fr, runs_56, max_runs=500, loose=False)
        if len(tm_56) > 200:
            return {"family": "sweep", "boat": "2", "crew": 2, "runs": runs_56, "time_map": tm_56}

    runs_136 = find_runs(fr, n, 4, 8, [136], loose=False)
    if runs_136:
        tm_136 = build_time_map_general(fr, runs_136, max_runs=500, loose=False)
        stats8 = seat_stats_sweep(fr, tm_136, 8)
        active8 = sum(1 for s in stats8 if is_active(s))
        # Accept as 8+ with up to 3 missing/broken sensors (>=5 active).
        # Also accept with >=3 active if runs_136 is large, stride-136 records
        # almost exclusively appear in 8+ boats, so if we found them the boat
        # is almost certainly an 8 regardless of silent sensors.
        if active8 >= 5 or (active8 >= 3 and len(tm_136) > 100):
            if active8 < 8:
                inactive = [i + 1 for i, s in enumerate(stats8) if not is_active(s)]
                logger.warning(
                    'detect_layout: 8+ detected with %d/%d active seats; '
                    'silent positions (1-indexed): %s',
                    active8, 8, inactive,
                )
            return {"family": "sweep", "boat": "8", "crew": 8, "runs": runs_136, "time_map": tm_136}

    # stride-120: 8+ boat with 1 disconnected sensor (22 + 7×14 = 120 bytes/record)
    runs_120 = find_runs(fr, n, 4, 8, [120], loose=False)
    if runs_120:
        tm_120 = build_time_map_general(fr, runs_120, max_runs=500, loose=False)
        if len(tm_120) > 100:
            stats7 = seat_stats_sweep(fr, tm_120, 7)
            active7 = sum(1 for s in stats7 if is_active(s))
            if active7 >= 5:
                logger.info(
                    'detect_layout: stride-120 detected, 8+ with %d/7 active slots '
                    '(1 sensor disconnected)',
                    active7,
                )
                return {"family": "sweep", "boat": "8", "crew": 8, "active_slots": 7,
                        "runs": runs_120, "time_map": tm_120}

    runs_80_scull = find_runs(fr, n, 4, 8, [80], loose=True)
    tm_80_scull = build_time_map_scull(fr, runs_80_scull, max_runs=300)
    runs_80_sweep = find_runs(fr, n, 4, 8, [80], loose=False)
    tm_80_sweep = build_time_map_general(fr, runs_80_sweep, max_runs=500, loose=False)

    def scull_activity_score(time_map):
        if not time_map:
            return -1e9
        items = list(time_map.items())[:500]
        scores = []
        for seat in range(2):
            base = seat * SCULL_SEAT_STRIDE
            l_nz = r_nz = t_nz = 0
            for _, pos in items:
                try:
                    l = max(0, fr.u16(pos + SCULL_LEFT_REL + base) - 1001)
                    r = max(0, fr.u16(pos + SCULL_RIGHT_REL + base) - 1001)
                    t = max(0, fr.u16(pos + SCULL_TOTAL_REL + base) - 1001)
                except Exception:
                    l = r = t = 0
                if l > 0:
                    l_nz += 1
                if r > 0:
                    r_nz += 1
                if t > 0 or (l + r) > 0:
                    t_nz += 1
            scores.append((l_nz + r_nz + t_nz) / max(1, len(items)))
        return sum(scores)

    def sweep4_activity_score(time_map):
        if not time_map:
            return -1e9
        stats4 = seat_stats_sweep(fr, time_map, 4)
        return sum(2.0 if is_active(s) else 0.0 for s in stats4)

    if scull_activity_score(tm_80_scull) >= sweep4_activity_score(tm_80_sweep) and len(tm_80_scull) > 150:
        return {"family": "scull", "boat": "2x", "crew": 2, "runs": runs_80_scull, "time_map": tm_80_scull}
    if len(tm_80_sweep) > 150:
        return {"family": "sweep", "boat": "4", "crew": 4, "runs": runs_80_sweep, "time_map": tm_80_sweep}

    raise RuntimeError("Could not confidently detect boat type")


def _time_guard_from_current(time_map: dict[int, int]) -> tuple[int, int] | None:
    if not time_map:
        return None
    first = min(time_map)
    last = max(time_map)
    margin_before = max(30_000, min(120_000, int(first * 0.10)))
    return int(first - margin_before), int(last + 120_000)


def _side_band_gap_ranges(peach_path: Path, boat_type: str) -> tuple[list[tuple[int, int]], tuple[int, int]]:
    """Return non-50Hz byte ranges where periodic side-band records can live."""
    reader = None
    try:
        reader = PeachBinaryReader(peach_path, boat_type=boat_type)
        block_offsets = sorted(int(offset) for offset in reader._block_offsets)
        block_size = int(reader._block_size)
        time_range = tuple(int(v) for v in reader.time_range_ms)
    except Exception as exc:
        logger.debug("side-band periodic recovery unavailable for %s: %s", peach_path, exc)
        return [], (0, 0)
    finally:
        if reader is not None and hasattr(reader, "close"):
            try:
                reader.close()
            except Exception:
                pass

    if not block_offsets or block_size < 1000:
        return [], time_range

    gap_ranges: list[tuple[int, int]] = []
    prev_end = 0
    for block_offset in block_offsets:
        gap_start = max(0, prev_end)
        gap_end = block_offset
        if gap_end > gap_start:
            gap_ranges.append((gap_start, gap_end))
        prev_end = max(prev_end, block_offset + block_size)
    file_size = peach_path.stat().st_size
    if prev_end < file_size:
        gap_ranges.append((prev_end, file_size))
    return gap_ranges, time_range


def _scull_record_min_bytes(crew: int) -> int:
    return SCULL_TOTAL_REL + (crew - 1) * SCULL_SEAT_STRIDE + 2


def _is_plausible_scull_side_band_record(
    fr: FastReader,
    pos: int,
    time_range_ms: tuple[int, int],
    crew: int,
) -> bool:
    try:
        if not row_sanity(fr, pos, loose=True):
            return False
        time_ms = fr.u32(pos)
        if not (time_range_ms[0] <= time_ms <= time_range_ms[1] + 10_000):
            return False
        any_power = False
        for seat in range(crew):
            base = seat * SCULL_SEAT_STRIDE
            total = fr.u16(pos + SCULL_TOTAL_REL + base) - 1001
            left = fr.u16(pos + SCULL_LEFT_REL + base) - 1001
            right = fr.u16(pos + SCULL_RIGHT_REL + base) - 1001
            if not (0 <= total <= SCULL_SIDE_BAND_MAX_POWER_W):
                return False
            if not (-1001 <= left <= SCULL_SIDE_BAND_MAX_POWER_W):
                return False
            if not (-1001 <= right <= SCULL_SIDE_BAND_MAX_POWER_W):
                return False
            any_power = any_power or total > 0 or left > 0 or right > 0
        return any_power
    except Exception:
        return False


def _scan_scull_side_band_time_map(
    peach_path: Path,
    fr: FastReader,
    layout: dict,
) -> dict[int, int]:
    boat_type = str(layout.get("boat") or "")
    crew = int(layout.get("crew") or 0)
    if boat_type not in SCULL_SIDE_BAND_BOATS or crew not in (1, 2):
        return {}

    gap_ranges, time_range = _side_band_gap_ranges(peach_path, boat_type)
    if not gap_ranges:
        return {}

    guard = _time_guard_from_current(dict(layout.get("time_map") or {}))
    min_bytes = _scull_record_min_bytes(crew)
    side_time_map: dict[int, int] = {}
    for gap_start, gap_end in gap_ranges:
        last_start_exclusive = max(gap_start, gap_end - min_bytes + 1)
        for pos in range(gap_start, last_start_exclusive, 4):
            try:
                time_ms = fr.u32(pos)
            except Exception:
                continue
            if guard is not None and not (guard[0] <= time_ms <= guard[1]):
                continue
            if not _is_plausible_scull_side_band_record(fr, pos, time_range, crew):
                continue
            side_time_map.setdefault(int(time_ms), pos)
    return dict(sorted(side_time_map.items()))


def _metric_fields_plausible_for_sweep_side_band(fr: FastReader, pos: int) -> bool:
    try:
        stroke_no = fr.u16(pos + SWEEP_STROKE_REL)
    except Exception:
        return False
    if not (0 < stroke_no < 10_000):
        return False
    if row_sanity(fr, pos, loose=True):
        return True
    try:
        rating_raw = fr.u8(pos + RATING_REL)
        speed_raw = fr.u16(pos + SPEED_REL)
        dist_raw = fr.u16(pos + DIST_REL)
    except Exception:
        return False
    return rating_raw == 0 and speed_raw == 0 and dist_raw == 0


def _sweep_min_record_bytes(crew: int, active_slots: int) -> int:
    if crew == 2:
        return PAIR_POWER_REL + PAIR_STRIDE + 2
    return SWEEP_POWER_REL + (active_slots - 1) * SWEEP_STRIDE + 2


def _decode_sweep_side_band_powers(
    fr: FastReader,
    pos: int,
    crew: int,
    active_slots: int,
) -> list[float] | None:
    if crew == 2:
        try:
            seat1_live_raw = fr.u16(pos + PAIR_SEAT1_CURRENT_REL)
            seat1_duplicate_raw = fr.u16(pos + PAIR_POWER_REL)
            seat2_raw = fr.u16(pos + PAIR_POWER_REL + PAIR_STRIDE)
        except Exception:
            return None
        if seat1_live_raw and seat1_duplicate_raw not in (0, seat1_live_raw):
            return None
        raw_values = [seat1_live_raw, seat2_raw]
    else:
        raw_values = []
        for seat in range(active_slots):
            try:
                raw_values.append(fr.u16(pos + SWEEP_POWER_REL + seat * SWEEP_STRIDE))
            except Exception:
                return None
        raw_values.extend([0] * max(0, crew - active_slots))

    powers: list[float] = []
    for raw in raw_values[:crew]:
        if raw == 0:
            powers.append(0.0)
            continue
        if raw < 1001:
            return None
        power = float(raw - 1001)
        if power < 0 or power > SWEEP_SIDE_BAND_MAX_POWER_W:
            return None
        powers.append(power)
    return powers


def _scan_sweep_side_band_time_map(
    peach_path: Path,
    fr: FastReader,
    layout: dict,
) -> dict[int, int]:
    boat_type = str(layout.get("boat") or "")
    if boat_type not in {"2", "4", "8"}:
        return {}

    gap_ranges, time_range = _side_band_gap_ranges(peach_path, boat_type)
    if not gap_ranges:
        return {}

    crew = int(layout.get("crew") or int(boat_type))
    active_slots = int(layout.get("active_slots") or crew)
    min_bytes = _sweep_min_record_bytes(crew, active_slots)
    guard = _time_guard_from_current(dict(layout.get("time_map") or {}))
    side_time_map: dict[int, int] = {}
    for gap_start, gap_end in gap_ranges:
        last_start_exclusive = max(gap_start, gap_end - min_bytes + 1)
        for pos in range(gap_start, last_start_exclusive, 4):
            try:
                time_ms = fr.u32(pos)
            except Exception:
                continue
            if not (time_range[0] <= time_ms <= time_range[1] + 10_000):
                continue
            if guard is not None and not (guard[0] <= time_ms <= guard[1]):
                continue
            if not _metric_fields_plausible_for_sweep_side_band(fr, pos):
                continue
            powers = _decode_sweep_side_band_powers(fr, pos, crew, active_slots)
            if powers is None or not any(power > 0 for power in powers):
                continue
            side_time_map.setdefault(int(time_ms), pos)
    return dict(sorted(side_time_map.items()))


def _find_gps_blocks(data: bytes) -> list[int]:
    """
    Scan binary for 352-byte GPS blocks.
    GPS blocks have: u32 marker=352 at offset 0, u32=32 at offset 20.
    Each block contains 10 sub-records of 16 u16 values (1 Hz GPS).
    """
    n = len(data)
    offsets = []
    pos = 0
    while pos + 352 <= n:
        marker = struct.unpack_from('<I', data, pos)[0]
        if marker == 352 and pos + 28 <= n:
            v5 = struct.unpack_from('<I', data, pos + 20)[0]
            if v5 == 32:
                offsets.append(pos)
                pos += 352
                continue
        pos += 4
    return offsets


def _extract_gps_init(data: bytes) -> tuple[float | None, float | None]:
    """
    Extract the GPS initialisation coordinates embedded in the .peach-data binary.

    The Peach PowerLine firmware writes a fixed 16-word (uint16) marker pattern
    somewhere before the first GPS data block.  The two int32 values at bytes
    25-32 of a nearby block encode (latitude, longitude) as ``int32 / 2^23``
    in degrees.

    Uses vectorized numpy correlation for fast search (~ms on 22MB files).

    Returns (lat_deg, lon_deg) or (None, None) if the marker is not found.
    """
    # Fixed 16-word pattern that precedes the GPS init coordinate block
    GPS_LOC_FLAG = np.array(
        [20, 0, 0, 0, 0, 0, 0, 48, 0, 0, 0, 0, 0, 0, 0, 17],
        dtype=np.uint16,
    )

    even_data = data[:len(data) // 2 * 2]
    bin_u16 = np.frombuffer(even_data, dtype=np.uint16)
    flag_len = len(GPS_LOC_FLAG)
    n = len(bin_u16)

    if n < flag_len + 31:
        return (None, None)

    # Vectorized search: narrow candidates using the rare non-zero values first
    # Flag[0]=20 is distinctive, find all positions where bin_u16 == 20
    candidates = np.where(bin_u16[:n - flag_len] == 20)[0]

    for i in candidates:
        if np.array_equal(bin_u16[i : i + flag_len], GPS_LOC_FLAG):
            if i + 31 > n:
                continue
            block = bin_u16[i + 7 : i + 31]
            if block.size < 24:
                continue
            block_u8 = block.view(np.uint8)
            if len(block_u8) < 33:
                continue
            coords = block_u8[25:33].view(np.int32) / 2**23  # (lat, lon) °
            lat, lon = float(coords[0]), float(coords[1])
            if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                return (lat, lon)

    return (None, None)


def extract_gps_from_blocks(
    data: bytes,
    ref_lat: float | None = None,
    ref_lon: float | None = None,
) -> list[dict]:
    """
    Extract GPS coordinates from 352-byte GPS blocks in .peach-data binary.

    GPS block format:
      - 28-byte header: [u32:352, u32:ref, u32:?, u32:?, u32:session_const,
                          u32:32, u32:block_start_ms]
      - 10 × 32-byte sub-records (16 u16 values each)
      - 4 bytes padding

    Sub-record columns (verified against Peach Excel exports):
      Col 3:  GPS X Lo , (raw - 1) / 256  = meters north offset (fractional, 0-64 m)
      Col 4:  GPS X Hi , (raw - 8192) * 64 = meters north offset (coarse, multiples of 64 m)
      Col 5:  GPS Y Lo , (raw - 1) / 256  = meters east  offset (fractional, 0-64 m)
      Col 6:  GPS Y Hi , (raw - 8192) * 64 = meters east  offset (coarse, multiples of 64 m)
      Col 7:  GPS RTCM / sequence counter
      Col 8:  GPS status (257 = GPS fix, 769 = DGPS fix, 0 = no fix)

    Encoding:
      offset_north_m = X_Hi + X_Lo = (col4 - 8192) * 64 + (col3 - 1) / 256
      offset_east_m  = Y_Hi + Y_Lo = (col6 - 8192) * 64 + (col5 - 1) / 256
      lat = ref_lat + offset_north_m / 111139.0
      lon = ref_lon + offset_east_m  / (111139.0 * cos(ref_lat))

    The reference coordinates (ref_lat, ref_lon) represent the GPS receiver's
    first-fix position.  They are embedded in the .peach-data binary as a pair
    of int32 values (encoded as int32 / 2^23 degrees) following a fixed 16-word
    marker pattern.  If not provided explicitly, they are extracted automatically.

    Returns list of {'time_ms', 'lat_deg', 'lon_deg', 'lat_semi', 'lon_semi'}.
    """
    _log = logging.getLogger(__name__)

    gps_offsets = _find_gps_blocks(data)
    if not gps_offsets:
        _log.info("No 352-byte GPS blocks found in file")
        return []

    # Resolve reference coordinates from binary or explicit parameters
    if ref_lat is None or ref_lon is None:
        ref_lat, ref_lon = _extract_gps_init(data)
    if ref_lat is None or ref_lon is None:
        _log.warning(
            "GPS init coordinates not found in .peach-data binary.  "
            "GPS will be omitted."
        )
        return []

    _log.info("GPS reference: lat=%.6f  lon=%.6f  (%d GPS blocks found)",
              ref_lat, ref_lon, len(gps_offsets))

    cos_ref = math.cos(math.radians(ref_lat))
    SEMI_PER_DEG = 2**31 / 180.0

    gps_points = []

    for block_off in gps_offsets:
        # Header field [6] = block start time in ms
        block_start_ms = struct.unpack_from('<I', data, block_off + 24)[0]

        for sub_idx in range(10):
            rec_off = block_off + 28 + sub_idx * 32
            vals = struct.unpack_from('<16H', data, rec_off)

            # GPS status check: col8 > 0 means valid fix
            if vals[8] == 0:
                continue

            # Timestamp: derive from block header + sub-record position
            # (sub-records are 1 Hz within each 10-second block)
            time_ms = block_start_ms + (sub_idx + 1) * 1000

            # Decode GPS coordinates (verified against Peach Excel exports)
            # X (latitude):  Hi = (col4 - 8192) * 64,  Lo = (col3 - 1) / 256
            x_hi = (vals[4] - 8192) * 64          # coarse meters (multiple of 64)
            x_lo = (vals[3] - 1) / 256.0           # fine meters (0 … ~64)
            offset_north_m = x_hi + x_lo

            # Y (longitude): Hi = (col6 - 8192) * 64,  Lo = (col5 - 1) / 256
            y_hi = (vals[6] - 8192) * 64
            y_lo = (vals[5] - 1) / 256.0
            offset_east_m = y_hi + y_lo

            # Convert to WGS84 degrees
            lat_deg = ref_lat + offset_north_m / 111139.0
            lon_deg = ref_lon + offset_east_m / (111139.0 * cos_ref)

            # Sanity check: reasonable GPS coordinates
            if not (-90.0 <= lat_deg <= 90.0 and -180.0 <= lon_deg <= 180.0):
                continue

            # Convert to FIT semicircles
            lat_semi = int(round(lat_deg * SEMI_PER_DEG))
            lon_semi = int(round(lon_deg * SEMI_PER_DEG))

            gps_points.append({
                'time_ms': time_ms,
                'lat_deg': lat_deg,
                'lon_deg': lon_deg,
                'lat_semi': lat_semi,
                'lon_semi': lon_semi,
            })

    if gps_points:
        _log.info("GPS extracted: %d points, lat=[%.6f, %.6f], lon=[%.6f, %.6f]",
                  len(gps_points),
                  min(p['lat_deg'] for p in gps_points),
                  max(p['lat_deg'] for p in gps_points),
                  min(p['lon_deg'] for p in gps_points),
                  max(p['lon_deg'] for p in gps_points))
    else:
        _log.info("No valid GPS data decoded from %d blocks", len(gps_offsets))

    return gps_points


# ---------------------------------------------------------------------------
# Session model
# ---------------------------------------------------------------------------

BOATS = {"1x": 1, "2x": 2, "2-": 2, "4-": 4, "8+": 8, "4x": 4}
_LAYOUT_NAME = {"2-": "2", "4-": "4", "8+": "8"}
UNTESTED_BOATS = {"4x"}


@dataclass
class SeatPower:
    """Power for one seat, one value per stroke row. NaN means "not reported"."""

    total: np.ndarray
    port: np.ndarray | None = None
    starboard: np.ndarray | None = None


@dataclass
class StrokeSession:
    path: Path
    boat: str
    sculling: bool
    crew: int
    time_ms: np.ndarray          # session clock at the END of each stroke
    rate_spm: np.ndarray
    speed_mps: np.ndarray
    distance_m: np.ndarray       # distance covered in the stroke, device value
    seats: list[SeatPower]
    header_time: datetime | None  # logger clock at session time 0, usually LOCAL time
    gps: list[dict] = field(default_factory=list)
    cumulative_m: np.ndarray | None = None
    samples: dict[str, np.ndarray] | None = None


def _watts(raw: int) -> float:
    """Raw u16 to watts. 0 is "no value"; 1001 is a measured 0 W."""
    return float("nan") if raw == 0 else float(max(0, raw - POWER_OFFSET))


def _row_powers(fr: FastReader, pos: int, sculling: bool, crew: int, active: int) -> list[tuple]:
    out: list[tuple] = []
    for seat in range(crew):
        try:
            if sculling:
                base = pos + seat * SCULL_SEAT_STRIDE
                port = _watts(fr.u16(base + SCULL_RIGHT_REL))
                stbd = _watts(fr.u16(base + SCULL_LEFT_REL))
                # The separate total is NOT used: when both sides are missing it
                # repeats the previous stroke (431 of 431 cases checked).
                both = [v for v in (port, stbd) if not math.isnan(v)]
                out.append((sum(both) if both else float("nan"), port, stbd))
            elif seat >= active:
                out.append((float("nan"), None, None))
            elif crew == 2:
                out.append((_watts(fr.u16(pos + PAIR_POWER_REL + seat * PAIR_STRIDE)), None, None))
            else:
                # 12 bytes further the device keeps the last known value. That is a
                # repeat, not a measurement, so it is not read.
                out.append((_watts(fr.u16(pos + SWEEP_POWER_REL + seat * SWEEP_STRIDE)), None, None))
        except (struct.error, IndexError):
            out.append((float("nan"), None, None))
    return out


def read_session(path: str | Path, boat: str = "auto", *, experimental: bool = False,
                 with_samples: bool = True) -> StrokeSession:
    """Read stroke rows (and 50 Hz samples) from a ``.peach-data`` file."""
    path = Path(path)
    data = path.read_bytes()
    fr = FastReader(data)
    if boat != "auto" and boat not in BOATS:
        raise ValueError(f"unknown boat {boat!r}, expected one of {sorted(BOATS)}")
    layout = detect_layout(fr, data, _LAYOUT_NAME.get(boat, boat) if boat != "auto" else "auto")
    found = {v: k for k, v in _LAYOUT_NAME.items()}.get(layout["boat"], layout["boat"])
    if found in UNTESTED_BOATS and not experimental:
        raise ValueError(
            f"{found} has never been checked against a reference file. Read "
            "docs/untested-boats.md, or pass --experimental to try anyway.")
    crew, sculling = int(layout["crew"]), layout["family"] == "scull"
    active = int(layout.get("active_slots") or crew)
    time_map = dict(layout["time_map"])
    extra = (_scan_scull_side_band_time_map if sculling else _scan_sweep_side_band_time_map)(path, fr, layout)
    for t, pos in extra.items():
        time_map.setdefault(t, pos)
    if not time_map:
        raise ValueError(f"{path.name}: no stroke rows found for boat {found}")

    times = np.array(sorted(time_map), dtype=float)
    rate, speed, dist, powers = [], [], [], []
    for t in sorted(time_map):
        pos = time_map[t]
        rate.append(max(0.0, 0.5 * fr.u8(pos + RATING_REL) - 0.5))
        speed.append(max(0.0, (fr.u16(pos + SPEED_REL) - 1) / 256.0))
        dist.append(max(0.0, (fr.u16(pos + DIST_REL) - 1) / 256.0))
        powers.append(_row_powers(fr, pos, sculling, crew, active))
    seats = []
    for s in range(crew):
        col = lambda k: np.array([np.nan if p[s][k] is None else p[s][k] for p in powers], float)  # noqa: E731
        seats.append(SeatPower(col(0), col(1) if sculling else None, col(2) if sculling else None))

    ses = StrokeSession(path, found, sculling, crew, times, np.array(rate), np.array(speed),
                        np.array(dist), seats, extract_start_utc_from_peach_data(path))
    try:
        ses.gps = extract_gps_from_blocks(data)
    except (ValueError, struct.error) as exc:
        logger.warning("GPS not read: %s", exc)
    if with_samples:
        ses.samples = PeachBinaryReader(path, boat_type=layout["boat"]).to_dict(
            skip_inactive=False, include_unknown=False, compute_derived=True)
        ses.cumulative_m = _cumulative_distance(ses)
    return ses


def _cumulative_distance(ses: StrokeSession) -> np.ndarray | None:
    """Distance from the first stroke, from the 50 Hz boat speed."""
    d = ses.samples or {}
    if "Speed_Boat" not in d:
        return None
    t = d["Time"].astype(float)
    v = np.clip(np.nan_to_num(d["Speed_Boat"].astype(float)), 0, 15)
    dt = np.clip(np.diff(t, prepend=t[0]), 0, 100) / 1000.0
    cum = np.cumsum(v * dt)
    at = np.interp(ses.time_ms, t, cum)
    return np.maximum.accumulate(at - at[0])


def cycle_span_ms(ses: StrokeSession) -> np.ndarray:
    """Length of each stroke, which ENDS at its row time.

    The interval since the previous row, capped at the stroke period implied by
    the stroke rate plus 1 s, so a stop is never counted as part of a stroke.
    """
    gap = np.diff(ses.time_ms, prepend=ses.time_ms[0] - 3000.0)
    period = np.where(ses.rate_spm > 5, 60000.0 / np.maximum(ses.rate_spm, 5), 3000.0)
    return np.minimum(gap, period + 1000.0)


def seat_work_rate(ses: StrokeSession, seat: int) -> np.ndarray | None:
    """Mean |force x angular velocity| per stroke from the 50 Hz data, arbitrary units."""
    d = ses.samples
    if not d:
        return None
    t = d["Time"].astype(float)
    e = np.zeros(len(t))
    found = False
    for pre in ("", "S_"):
        f, w = d.get(f"{pre}GateForceX_{seat}"), d.get(f"{pre}GateAngleVel_{seat}")
        if f is not None and w is not None:
            e += np.abs(np.nan_to_num(f.astype(float)) * np.deg2rad(np.nan_to_num(w.astype(float))))
            found = True
    if not found:
        return None
    cum = np.r_[0.0, np.cumsum(e) * 0.02]
    span = cycle_span_ms(ses)
    a, b = np.searchsorted(t, ses.time_ms - span), np.searchsorted(t, ses.time_ms)
    return (cum[b] - cum[a]) / np.maximum(span / 1000.0, 0.1)


REST_FRACTION = 0.10


def classify_power(ses: StrokeSession, seat: int) -> tuple[np.ndarray, np.ndarray]:
    """Power per stroke to export, and a status per stroke.

    status: 0 reported, 1 not reported but the seat was clearly resting (0 W),
    2 not reported while the 50 Hz data shows work, or unknown (NaN, leave out).
    """
    p = ses.seats[seat - 1].total.copy()
    status = np.zeros(len(p), int)
    missing = np.isnan(p)
    status[missing] = 2
    w = seat_work_rate(ses, seat)
    if w is not None and (~missing).any():
        typical = np.nanmedian(w[(~missing) & (p > 20)]) if ((~missing) & (p > 20)).any() else np.nan
        if np.isfinite(typical) and typical > 0:
            rest = missing & (w < REST_FRACTION * typical)
            p[rest], status[rest] = 0.0, 1
    return p, status
