#!/usr/bin/env python3
"""
telemetry_engine.py, Stroke analysis: segmentation, curves, MMP inputs and technique metrics.

Reads 50Hz aperiodic data from .peach-data binary files and performs:
  - Session overview (per-stroke summary from periodic data)
  - Stroke segmentation (catch detection via GateAngle local minima)
  - Per-rower average stroke computation (200pt resampled mean ± std)
  - Per-rower parametric (angle vs force) and time-based curve extraction
  - Boat telemetry per stroke (speed, accel, roll, pitch, yaw)

This module is UI-independent, used by both the PySide6 app and CLI tools.
All heavy computation runs here; the UI just passes results to pyqtgraph/matplotlib.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
if not hasattr(np, 'trapezoid'):
    np.trapezoid = np.trapz  # NumPy < 2.0 compat

logger = logging.getLogger(__name__)

from rowtools.samples import PeachBinaryReader
from rowtools.strokes import extract_gps_from_blocks, read_session

# ── Constants ──────────────────────────────────────────────────────────────────

N_RESAMPLE = 200          # Points per average stroke curve
SAMPLE_RATE_HZ = 50       # 50Hz aperiodic data
DT_S = 1.0 / SAMPLE_RATE_HZ
# GateForceX channel is returned in Newtons by PeachBinaryReader (SCALE_FORCE=0.5).
# KGF_TO_N is kept as a conversion constant for threshold calculations only, do NOT
# multiply force channel values by KGF_TO_N (they are already in Newtons).
KGF_TO_N = 9.81
FORCE_THRESHOLD = 2.0     # kgf equivalent, kept for reference; use FORCE_THRESHOLD_N for comparisons
# Load detection also uses a per-stroke dynamic floor: max(FORCE_THRESHOLD_N, FORCE_THRESHOLD_PCT * peak_n)
FORCE_THRESHOLD_PCT = 0.05   # 5% of peak force, handles calibration offset / baseline drift
FORCE_THRESHOLD_N = FORCE_THRESHOLD * KGF_TO_N   # ≈19.6 N, use this for Newton-scale comparisons
OAR_MOMENT_ARM_M = 0.88   # Default moment arm (m), distance gate → oarlock
# GateAngleVel channel is in degrees/second (computed as finite-diff of GateAngle).
# All formulas that need rad/s must apply np.deg2rad() to the omega values.
# Slip / wash angle thresholds stored in kgf for user-facing configurability.
# Internally converted to Newtons (×KGF_TO_N) before comparing against force channels.
#   Catch Slip , angle swept from catch until blade force first reaches SLIP_THRESHOLD_KGF × KGF_TO_N
#   Finish Wash, angle swept after blade force drops below WASH_THRESHOLD_KGF × KGF_TO_N to finish
# Configurable via Settings → Telemetry; defaults match Peach factory settings.
SLIP_THRESHOLD_KGF: float = 30.0   # kgf (= 294 N), force must reach this to be considered "blade loaded"
WASH_THRESHOLD_KGF: float = 15.0   # kgf (= 147 N), below this at finish is considered "blade washed"
# Sculling-specific defaults (lower force per blade → lower thresholds)
SCULLING_SLIP_THRESHOLD_KGF: float = 15.0   # kgf (= 147 N), sculling catch slip detection
SCULLING_WASH_THRESHOLD_KGF: float = 8.0    # kgf (= 78.5 N), sculling finish wash detection


def _not_negative(value: float) -> float:
    """Clip at 0 but keep NaN: a value the device did not report is not 0 W."""
    return float('nan') if value is None or not np.isfinite(value) else max(0.0, float(value))


def _finite_values(values: np.ndarray) -> np.ndarray:
    """Return finite values only; used before NumPy nan reducers."""
    arr = np.asarray(values, dtype=np.float64)
    return arr[np.isfinite(arr)]


def _has_finite(values: np.ndarray) -> bool:
    return bool(np.any(np.isfinite(np.asarray(values, dtype=np.float64))))


def _safe_nanmean(values: np.ndarray, default: float = float('nan')) -> float:
    finite = _finite_values(values)
    return float(finite.mean()) if len(finite) else default


def _safe_nanmax(values: np.ndarray, default: float = float('nan')) -> float:
    finite = _finite_values(values)
    return float(finite.max()) if len(finite) else default


def _safe_nanmin(values: np.ndarray, default: float = float('nan')) -> float:
    finite = _finite_values(values)
    return float(finite.min()) if len(finite) else default


def _safe_nanargmax(values: np.ndarray) -> Optional[int]:
    arr = np.asarray(values, dtype=np.float64)
    valid = np.where(np.isfinite(arr))[0]
    if len(valid) == 0:
        return None
    return int(valid[np.argmax(arr[valid])])


def _safe_nanargmin(values: np.ndarray) -> Optional[int]:
    arr = np.asarray(values, dtype=np.float64)
    valid = np.where(np.isfinite(arr))[0]
    if len(valid) == 0:
        return None
    return int(valid[np.argmin(arr[valid])])


def _angle_signal_score(values: np.ndarray) -> float:
    arr = np.asarray(values, dtype=np.float64)
    finite = arr[np.isfinite(arr)]
    if len(finite) < 10 or len(arr) == 0:
        return 0.0
    coverage = len(finite) / len(arr)
    q05, q95 = np.percentile(finite, [5, 95])
    angle_range = float(q95 - q05)
    clipped = finite[(finite >= q05) & (finite <= q95)]
    angle_std = float(clipped.std()) if len(clipped) else 0.0
    if angle_range < 15.0 or angle_std < 3.0:
        return 0.0
    return angle_range * angle_std * coverage


def _select_reference_angle_column(data: Dict[str, np.ndarray], preferred: Optional[str] = None) -> Optional[str]:
    candidates: List[str] = []
    if preferred and preferred in data:
        candidates.append(preferred)
    candidates.extend(
        sorted(
            c for c in data
            if (c.startswith('GateAngle_') or c.startswith('S_GateAngle_')) and c not in candidates
        )
    )
    best_col = None
    best_score = 0.0
    for col in candidates:
        score = _angle_signal_score(data[col])
        if score > best_score:
            best_col = col
            best_score = score
    return best_col


def _fill_nan_for_detection(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    finite = np.isfinite(arr)
    if finite.all():
        return arr
    if not finite.any():
        return arr.copy()
    idx = np.arange(len(arr))
    return np.interp(idx, idx[finite], arr[finite])

# ── xlsx aperiodic column mapping ──────────────────────────────────────────────
# Maps xlsx column header → (internal_field_name, scale_factor)
# scale_factor converts xlsx value to our internal unit.
#
# UNITS IN PEACH XLSX (verified against testdata):
#   SwivelPower         → Watts        (×1, already W)
#   MinAngle / MaxAngle → degrees      (×1)
#   Drive Start T       → milliseconds (×1)  ← stored as ms, NOT seconds
#   Drive Time          → seconds      (×1000 to convert to ms)
#   Recovery Time       → seconds      (×1000 to convert to ms)
#
# The 'P ' and 'S ' prefixes used by the 1x export (e.g. 'P Min Angle') are
# stripped before lookup so we don't need separate entries.
# 'Rower Swivel Power' is Peach's sticky/rolling last-value column, same field.
XLSX_AP_METRIC_MAP: Dict[str, tuple] = {
    # ── Power ─────────────────────────────────────────────────────────────────
    'SwivelPower':          ('swivel_power',      1.0),
    'Swivel Power':         ('swivel_power',      1.0),
    'Rower Swivel Power':   ('swivel_power',      1.0),   # 1x export sticky column
    'Power':                ('swivel_power',      1.0),
    'AvgPower':             ('swivel_power',      1.0),
    'Avg Power':            ('swivel_power',      1.0),
    # ── Angles ────────────────────────────────────────────────────────────────
    'MinAngle':             ('catch_angle',        1.0),
    'Min Angle':            ('catch_angle',        1.0),
    'CatchAngle':           ('catch_angle',        1.0),
    'Catch Angle':          ('catch_angle',        1.0),
    'CatchSlip':            ('slip_angle',         1.0),
    'Catch Slip':           ('slip_angle',         1.0),
    'Slip':                 ('slip_angle',         1.0),
    'MaxAngle':             ('finish_angle',       1.0),
    'Max Angle':            ('finish_angle',       1.0),
    'FinishAngle':          ('finish_angle',       1.0),
    'Finish Angle':         ('finish_angle',       1.0),
    'FinishSlip':           ('wash_angle',         1.0),
    'Finish Slip':          ('wash_angle',         1.0),
    'Wash':                 ('wash_angle',         1.0),
    # ── Timing ────────────────────────────────────────────────────────────────
    # Drive Start T is stored in MILLISECONDS in the xlsx (e.g. 34-115 ms).
    # Drive Time and Recovery Time are stored in SECONDS → multiply by 1000.
    'Drive Start T':        ('drive_start_ms',     1.0),    # ms already
    'DriveStartT':          ('drive_start_ms',     1.0),
    'Drive Start':          ('drive_start_ms',     1.0),
    'Drive Time':           ('drive_time_ms',      1000.0), # seconds → ms
    'DriveTime':            ('drive_time_ms',      1000.0),
    'Drive T':              ('drive_time_ms',      1000.0),
    'Recovery Time':        ('recovery_time_ms',   1000.0), # seconds → ms
    'RecoveryTime':         ('recovery_time_ms',   1000.0),
    'Recovery T':           ('recovery_time_ms',   1000.0),
    # ── Other angles ─────────────────────────────────────────────────────────
    'Angle Max F':          ('angle_max_force',    1.0),
    'Angle MaxF':           ('angle_max_force',    1.0),
    'AngleMaxF':            ('angle_max_force',    1.0),
    'Angle 0.7 F':          ('angle_70pct_force',  1.0),
    'Angle 0.7F':           ('angle_70pct_force',  1.0),
    # ── Work quartiles ────────────────────────────────────────────────────────
    'Work PC Q1':           ('work_q1',            1.0),
    'Work PC Q2':           ('work_q2',            1.0),
    'Work PC Q3':           ('work_q3',            1.0),
    'Work PC Q4':           ('work_q4',            1.0),
    'WorkPCQ1':             ('work_q1',            1.0),
    'WorkPCQ2':             ('work_q2',            1.0),
    'WorkPCQ3':             ('work_q3',            1.0),
    'WorkPCQ4':             ('work_q4',            1.0),
    'Max Force PC':         ('max_force_pc',       1.0),
}

ROWER_CHANNELS = ['GateAngle', 'GateForceX', 'GateForceY', 'GateAngleVel']
# Sculling (strokeside) variants, same channels with S_ prefix
ROWER_CHANNELS_S = ['S_GateAngle', 'S_GateForceX', 'S_GateForceY', 'S_GateAngleVel']
BOAT_CHANNELS = ['Speed_Boat', 'Accel_Boat', 'Roll_Boat', 'Pitch_Boat', 'Yaw_Boat',
                 'Distance_Boat']

ROWER_COLORS = [
    '#e6194b', '#3cb44b', '#4363d8', '#f58231',
    '#911eb4', '#42d4f4', '#f032e6', '#bfef45'
]
AVG_COLOR = '#1a1a1a'
PORT_COLOR = '#22a355'      # green, port side
STBD_COLOR = '#d9363e'      # red, strokeside / starboard


# ── Data Classes ───────────────────────────────────────────────────────────────

@dataclass
class StrokeInfo:
    """A single stroke (catch-to-catch) extracted from 50Hz data."""
    index: int
    start_idx: int          # Index into the aperiodic array
    end_idx: int
    time_ms: float          # Start time in session-ms
    time_end_ms: float
    duration_ms: float
    rate_spm: float         # Instantaneous stroke rate
    channels: Dict[str, np.ndarray] = field(default_factory=dict, repr=False)
    # Index into the peach_rows list used during segmentation (None if not from Peach timestamps).
    # The corresponding peach_rows[peach_row_index] holds the device-computed power for this stroke.
    peach_row_index: Optional[int] = field(default=None, repr=False)

    @property
    def n_samples(self) -> int:
        return self.end_idx - self.start_idx + 1


@dataclass
class RowerCurves:
    """Average stroke curves for one rower."""
    seat: int
    n_strokes: int

    # Time-based (normalized, catch at 0% via circular shift)
    angle_mean: np.ndarray       # [N_RESAMPLE]
    angle_std: np.ndarray
    forcex_mean: np.ndarray
    forcex_std: np.ndarray
    forcey_mean: Optional[np.ndarray]   # None for seats 5-8 (1D pins)
    forcey_std: Optional[np.ndarray]
    anglevel_mean: np.ndarray
    anglevel_std: np.ndarray

    # Parametric (raw, unshifted, for Angle vs Force loops)
    angle_raw_mean: np.ndarray
    forcex_raw_mean: np.ndarray
    forcex_raw_std: np.ndarray
    seatpos_mean: Optional[np.ndarray] = None
    seatpos_std: Optional[np.ndarray] = None
    seatposvel_mean: Optional[np.ndarray] = None
    seatposvel_std: Optional[np.ndarray] = None
    seatpos_raw_mean: Optional[np.ndarray] = None
    seatpos_raw_std: Optional[np.ndarray] = None
    seatposvel_raw_mean: Optional[np.ndarray] = None
    seatposvel_raw_std: Optional[np.ndarray] = None
    stretcherforcex_mean: Optional[np.ndarray] = None
    stretcherforcex_std: Optional[np.ndarray] = None
    stretcherz_mean: Optional[np.ndarray] = None
    stretcherz_std: Optional[np.ndarray] = None
    stretcherrl_mean: Optional[np.ndarray] = None
    stretcherrl_std: Optional[np.ndarray] = None
    stretchertb_mean: Optional[np.ndarray] = None
    stretchertb_std: Optional[np.ndarray] = None
    stretcherforcex_raw_mean: Optional[np.ndarray] = None
    stretcherforcex_raw_std: Optional[np.ndarray] = None
    stretcherz_raw_mean: Optional[np.ndarray] = None
    stretcherz_raw_std: Optional[np.ndarray] = None
    stretcherrl_raw_mean: Optional[np.ndarray] = None
    stretcherrl_raw_std: Optional[np.ndarray] = None
    stretchertb_raw_mean: Optional[np.ndarray] = None
    stretchertb_raw_std: Optional[np.ndarray] = None

    # Individual strokes for overlay (list of resampled arrays)
    strokes_angle_raw: List[np.ndarray] = field(default_factory=list, repr=False)
    strokes_forcex_raw: List[np.ndarray] = field(default_factory=list, repr=False)


@dataclass
class BoatCurves:
    """Average per-stroke boat telemetry."""
    n_strokes: int
    speed_mean: np.ndarray
    speed_std: np.ndarray
    accel_mean: np.ndarray
    accel_std: np.ndarray
    roll_mean: np.ndarray
    roll_std: np.ndarray
    pitch_mean: np.ndarray
    pitch_std: np.ndarray
    yaw_mean: np.ndarray
    yaw_std: np.ndarray


@dataclass
class PeriodicSummary:
    """Per-stroke periodic summary for a piece (from stroke segmentation)."""
    stroke_index: np.ndarray     # [n_strokes]
    time_ms: np.ndarray          # Start time of each stroke
    rate_spm: np.ndarray         # Stroke rate
    duration_ms: np.ndarray      # Stroke duration
    # Per-rower peak force per stroke
    peak_force: Dict[int, np.ndarray] = field(default_factory=dict)
    # Boat metrics per stroke
    avg_speed: np.ndarray = field(default_factory=lambda: np.array([]))
    avg_accel: np.ndarray = field(default_factory=lambda: np.array([]))
    # Per-rower aperiodic per-stroke metrics (seat → array[n_strokes])
    # Names follow PowerLine's Excel column names
    catch_angle: Dict[int, np.ndarray] = field(default_factory=dict)        # MinAngle , degrees
    finish_angle: Dict[int, np.ndarray] = field(default_factory=dict)       # MaxAngle , degrees
    slip_angle: Dict[int, np.ndarray] = field(default_factory=dict)         # CatchSlip, degrees
    wash_angle: Dict[int, np.ndarray] = field(default_factory=dict)         # FinishSlip, degrees
    arc_length: Dict[int, np.ndarray] = field(default_factory=dict)         # ArcLength  = MaxAngle - MinAngle
    effective_arc: Dict[int, np.ndarray] = field(default_factory=dict)      # EffArcLen  = arc - slip - wash
    drive_start_ms: Dict[int, np.ndarray] = field(default_factory=dict)     # DriveStartT, ms from catch to load
    drive_time_ms: Dict[int, np.ndarray] = field(default_factory=dict)      # DriveTime, ms blade loaded
    recovery_time_ms: Dict[int, np.ndarray] = field(default_factory=dict)   # RecovTime, ms after unload
    ratio: Dict[int, np.ndarray] = field(default_factory=dict)              # drive / (drive + recovery)
    angle_max_force: Dict[int, np.ndarray] = field(default_factory=dict)    # AngleMaxF, ° at peak force
    angle_70pct_force: Dict[int, np.ndarray] = field(default_factory=dict)  # Angle0.7F, ° at 70% max F
    work_j: Dict[int, np.ndarray] = field(default_factory=dict)             # Work , Joules
    swivel_power: Dict[int, np.ndarray] = field(default_factory=dict)       # SwivelPower, W (mean F×ω×r)
    left_power: Dict[int, np.ndarray] = field(default_factory=dict)         # Port oar power, W (sculling, from device)
    right_power: Dict[int, np.ndarray] = field(default_factory=dict)        # Stbd oar power, W (sculling, from device)
    # Sculling starboard angles per stroke
    catch_angle_stbd: Dict[int, np.ndarray] = field(default_factory=dict)
    finish_angle_stbd: Dict[int, np.ndarray] = field(default_factory=dict)
    slip_angle_stbd: Dict[int, np.ndarray] = field(default_factory=dict)
    wash_angle_stbd: Dict[int, np.ndarray] = field(default_factory=dict)
    effective_arc_stbd: Dict[int, np.ndarray] = field(default_factory=dict)
    work_q1: Dict[int, np.ndarray] = field(default_factory=dict)            # WorkPCQ1, % work in Q1
    work_q2: Dict[int, np.ndarray] = field(default_factory=dict)            # WorkPCQ2, % work in Q2
    work_q3: Dict[int, np.ndarray] = field(default_factory=dict)            # WorkPCQ3, % work in Q3
    work_q4: Dict[int, np.ndarray] = field(default_factory=dict)            # WorkPCQ4, % work in Q4


@dataclass
class PieceAverages:
    """Average metrics for a piece, shown in the summary table."""
    avg_rate: float                      # SPM
    avg_speed: float                     # m/s
    avg_distance_per_stroke: float       # m
    duration_s: float
    n_strokes: int
    # Per-rower averages: seat → value
    avg_power: Dict[int, float] = field(default_factory=dict)
    avg_angle_range: Dict[int, float] = field(default_factory=dict)     # max - min gate angle
    peak_force: Dict[int, float] = field(default_factory=dict)          # avg peak ForceX
    # Sculling per-side: seat → (port_val, stbd_val)
    avg_power_port: Dict[int, float] = field(default_factory=dict)
    avg_power_stbd: Dict[int, float] = field(default_factory=dict)
    avg_angle_range_port: Dict[int, float] = field(default_factory=dict)
    avg_angle_range_stbd: Dict[int, float] = field(default_factory=dict)
    peak_force_port: Dict[int, float] = field(default_factory=dict)
    peak_force_stbd: Dict[int, float] = field(default_factory=dict)
    # Per-rower aperiodic averages (seat → scalar), names as in PowerLine's Excel export
    avg_catch_angle: Dict[int, float] = field(default_factory=dict)         # MinAngle
    avg_finish_angle: Dict[int, float] = field(default_factory=dict)        # MaxAngle
    avg_slip_angle: Dict[int, float] = field(default_factory=dict)          # CatchSlip
    avg_wash_angle: Dict[int, float] = field(default_factory=dict)          # FinishSlip
    avg_arc_length: Dict[int, float] = field(default_factory=dict)          # ArcLength
    avg_effective_arc: Dict[int, float] = field(default_factory=dict)       # EffArcLen
    avg_drive_start_ms: Dict[int, float] = field(default_factory=dict)      # DriveStartT
    avg_drive_time_ms: Dict[int, float] = field(default_factory=dict)       # DriveTime (blade loaded)
    avg_recovery_time_ms: Dict[int, float] = field(default_factory=dict)    # RecovTime (after unload)
    avg_ratio: Dict[int, float] = field(default_factory=dict)               # drive / (drive+recovery)
    avg_angle_max_force: Dict[int, float] = field(default_factory=dict)     # AngleMaxF
    avg_angle_70pct_force: Dict[int, float] = field(default_factory=dict)   # Angle0.7F
    avg_work_j: Dict[int, float] = field(default_factory=dict)              # Work (J)
    avg_swivel_power: Dict[int, float] = field(default_factory=dict)        # SwivelPower (W)
    avg_work_q1: Dict[int, float] = field(default_factory=dict)             # WorkPCQ1 (%)
    avg_work_q2: Dict[int, float] = field(default_factory=dict)             # WorkPCQ2 (%)
    avg_work_q3: Dict[int, float] = field(default_factory=dict)             # WorkPCQ3 (%)
    avg_work_q4: Dict[int, float] = field(default_factory=dict)             # WorkPCQ4 (%)
    # Sculling port/starboard splits
    avg_catch_angle_port: Dict[int, float] = field(default_factory=dict)
    avg_finish_angle_port: Dict[int, float] = field(default_factory=dict)
    avg_catch_angle_stbd: Dict[int, float] = field(default_factory=dict)
    avg_finish_angle_stbd: Dict[int, float] = field(default_factory=dict)
    avg_swivel_power_port: Dict[int, float] = field(default_factory=dict)
    avg_swivel_power_stbd: Dict[int, float] = field(default_factory=dict)


@dataclass
class PieceResult:
    """Complete analysis result for a selected time segment (piece)."""
    start_ms: float
    end_ms: float
    n_strokes: int
    avg_rate: float
    avg_speed: float
    crew_size: int
    is_sculling: bool
    rower_names: Dict[int, str]       # seat → rower name
    rower_curves: Dict[int, RowerCurves]
    rower_curves_s: Dict[int, RowerCurves]   # Strokeside curves (sculling only)
    boat_curves: BoatCurves
    periodic: PeriodicSummary
    averages: PieceAverages
    strokes: List[StrokeInfo] = field(repr=False)
    start_utc: Optional[datetime] = None   # session start from file header


@dataclass
class OverviewData:
    """Lightweight session overview data for the top-level chart."""
    time_s: np.ndarray          # Per-stroke time in seconds
    boat_power: np.ndarray      # Total boat power per stroke
    boat_speed: np.ndarray      # Average boat speed per stroke
    boat_rate: np.ndarray       # Stroke rate per stroke
    gps_lat: Optional[np.ndarray]
    gps_lon: Optional[np.ndarray]
    gps_time_ms: Optional[np.ndarray]
    session_name: str
    start_utc: Optional[datetime]
    boat_type: str
    crew_size: int
    duration_s: float
    total_strokes: int


# ── Stroke Segmentation ───────────────────────────────────────────────────────

def segment_strokes(data: Dict[str, np.ndarray], ref_col: str = 'GateAngle_1',
                    min_len: int = 20, max_len: int = 200,
                    min_force_n: float = 5.0) -> List[StrokeInfo]:
    """
    Segment 50Hz data into strokes at catch points (local minima of GateAngle).

    Args:
        data: dict of column_name → numpy array (from PeachBinaryReader.to_dict)
        ref_col: reference GateAngle column for catch detection
        min_len: minimum samples per stroke (~0.4s at 50Hz)
        max_len: maximum samples per stroke (~4s at 50Hz)
        min_force_n: minimum peak force to consider stroke active

    Returns:
        List of StrokeInfo with per-channel data arrays
    """
    # Find a usable reference angle column. Seat 1 can be disconnected while
    # later seats still have clean rowing data, so do not blindly trust GateAngle_1.
    selected_ref = _select_reference_angle_column(data, ref_col)
    if selected_ref is None:
        return []
    ref_col = selected_ref

    angle_raw = data[ref_col].astype(np.float64)
    angle = _fill_nan_for_detection(angle_raw)
    time_ms = data['Time'].astype(np.float64)

    # Smooth for peak detection (kernel=7)
    kernel = np.ones(7) / 7
    angle_smooth = np.convolve(angle, kernel, mode='same')

    # Local minima (catch points) with window=15
    window = 15
    n = len(angle_smooth)
    is_min = np.zeros(n, dtype=bool)
    for i in range(window, n - window):
        local = angle_smooth[i - window: i + window + 1]
        if angle_smooth[i] == local.min():
            is_min[i] = True

    min_indices = np.where(is_min)[0]
    if len(min_indices) < 2:
        return []

    # Snap each smoothed minimum to the nearest raw minimum within ±window samples.
    # The 7-sample smoothing kernel shifts the detected catch 3-9° shallower than
    # the true raw catch angle. Snapping corrects the stroke window start.
    snapped = []
    for idx in min_indices:
        ss = max(0, idx - window)
        se = min(n, idx + window + 1)
        local_offset = int(np.argmin(angle[ss:se]))
        snapped.append(ss + local_offset)
    min_indices = np.array(snapped)

    # Collapse clusters (prevent double-detection)
    boundaries = [min_indices[0]]
    for idx in min_indices[1:]:
        if idx - boundaries[-1] > min_len // 2:
            boundaries.append(idx)

    # Extract strokes
    data_cols = [c for c in data if c != 'Time']
    strokes = []

    for i in range(len(boundaries) - 1):
        start, end = boundaries[i], boundaries[i + 1]
        stroke_len = end - start
        if not (min_len <= stroke_len <= max_len):
            continue

        duration = float(time_ms[end] - time_ms[start])
        if duration <= 0:
            continue

        rate = 60000.0 / duration  # SPM

        channels = {}
        for col in data_cols:
            channels[col] = data[col][start:end + 1].copy()

        stroke = StrokeInfo(
            index=len(strokes),
            start_idx=start,
            end_idx=end,
            time_ms=float(time_ms[start]),
            time_end_ms=float(time_ms[end]),
            duration_ms=duration,
            rate_spm=rate,
            channels=channels,
        )

        # Filter inactive strokes (peak force < threshold)
        max_force = 0
        for col in channels:
            if 'GateForceX' in col:
                peak = _safe_nanmax(np.abs(channels[col]), default=0.0)
                max_force = max(max_force, peak)
        if max_force < min_force_n:
            continue

        strokes.append(stroke)

    return strokes


# ── Peach-timestamp stroke segmentation ───────────────────────────────────────

def segment_strokes_from_peach_timestamps(
    data: Dict[str, np.ndarray],
    peach_rows: List[Dict],
    min_force_n: float = 5.0,
) -> List[StrokeInfo]:
    """
    Segment 50Hz data into strokes guided by Peach periodic row intervals.

    The Peach device per-stroke timestamp is logged at the FINISH of each stroke
    (oar extracted, angle near maximum, force near zero), NOT at the catch.  To
    build correctly-phased windows we must find the actual catch (GateAngle_1
    local minimum) inside each Peach-defined period [T_i, T_{i+1}].

    Algorithm:
      1. For every consecutive pair of Peach timestamps [T_i, T_{i+1}], locate the
         GateAngle_1 minimum within the 50Hz window.  That minimum is the catch.
      2. Build stroke windows [catch_k, catch_{k+1}] from consecutive catches.
         The window always starts at force ≈ 0 (blade just entering water), giving
         the correct Force-vs-Stroke-% shape expected in the graphs.
      3. Use the Peach inter-stroke period for duration_ms (= T_{i+1} - T_i), which
         is what the Peach device itself uses for power calculation.  The 50Hz
         window may be slightly shorter because it runs to the NEXT catch, not the
         next Peach timestamp.

    Benefit: All seats (1-8) share the same window boundaries, so per-seat angle
    offset issues (absolute encoding differences) no longer contaminate the arc /
    work / power calculations, the window covers each seat's complete stroke.

    Args:
        data:        50Hz channel dict from PeachBinaryReader.to_dict().
        peach_rows:  Sorted-ascending per-stroke rows from decode_sweep_rows /
                     decode_scull_rows.  Each row must have 'time' (ms).
        min_force_n: Minimum peak GateForceX (kgf) to keep a stroke as active.

    Returns:
        List of StrokeInfo, each starting at the catch (angle minimum).
        StrokeInfo.time_ms == catch timestamp in the 50Hz data.
        StrokeInfo.duration_ms == Peach inter-stroke period (for power accuracy).
    """
    if not peach_rows or len(peach_rows) < 2:
        return []

    time_ms   = data['Time'].astype(np.float64)
    n_total   = len(time_ms)
    data_cols = [c for c in data if c != 'Time']

    # ── Find reference angle column for catch detection ───────────────────────
    # Some sessions have disconnected front seats. Pick the first genuinely
    # rowing angle signal instead of assuming GateAngle_1 is usable.
    ref_col = _select_reference_angle_column(data, 'GateAngle_1')

    if ref_col is None:
        # No angle channel: fall back to plain time-based windowing
        logger.debug('segment_strokes_from_peach_timestamps: no angle channel found, '
                     'using plain Peach timestamp boundaries')
        return _segment_strokes_by_time(data, peach_rows, min_force_n)

    angle_raw = data[ref_col].astype(np.float64)
    angle = _fill_nan_for_detection(angle_raw)

    # Light smoothing (same kernel as segment_strokes) for robust minimum detection
    _k7 = np.ones(7) / 7.0
    if len(angle) >= 7:
        angle_smooth = np.convolve(angle, _k7, mode='same')
    else:
        angle_smooth = angle.copy()

    # ── Find one catch index per Peach interval ───────────────────────────────
    # For each [T_i, T_{i+1}] Peach interval find the GateAngle minimum (catch).
    # Also record the Peach interval index 'i' so that compute_periodic_summary
    # can retrieve the device-computed power for the matching peach_rows entry.
    catch_indices:   List[int]   = []
    peach_periods:   List[float] = []
    catch_peach_idx: List[int]   = []   # peach_rows index for each catch

    for i in range(len(peach_rows) - 1):
        t_lo   = float(peach_rows[i]['time'])
        t_hi   = float(peach_rows[i + 1]['time'])
        period = t_hi - t_lo
        if period <= 0:
            continue

        # 50Hz sample indices for this Peach interval
        ia = int(np.searchsorted(time_ms, t_lo, side='left'))
        ib = int(np.searchsorted(time_ms, t_hi, side='left')) - 1
        ib = min(max(ia, ib), n_total - 1)

        n_win = ib - ia + 1
        if n_win < 10:          # < 200 ms, not enough samples for a valid catch
            continue

        # Sanity check: angle must oscillate enough to indicate active rowing
        win_angle   = angle_smooth[ia:ib + 1]
        if not _has_finite(win_angle):
            continue
        angle_range = _safe_nanmax(win_angle, default=0.0) - _safe_nanmin(win_angle, default=0.0)
        if angle_range < 15.0:  # < 15° swing → idle / inactive block
            continue

        # The catch is the global minimum of the smoothed angle in this window.
        # Snap to the raw angle minimum within ±5 samples to avoid the smoothing
        # kernel shifting the catch 3-9° shallower than the true raw catch angle.
        local_min_smooth = _safe_nanargmin(win_angle)
        if local_min_smooth is None:
            continue
        snap_lo = max(0, local_min_smooth - 5)
        snap_hi = min(n_win, local_min_smooth + 6)
        raw_local_min = _safe_nanargmin(angle[ia + snap_lo: ia + snap_hi])
        if raw_local_min is None:
            continue
        local_min = snap_lo + raw_local_min
        catch_indices.append(ia + local_min)
        peach_periods.append(period)
        # FIX: a device row is stamped when its stroke ENDS, so the stroke whose catch
        # lies in [T_i, T_i+1] is row i+1. Row i gave every stroke its predecessor's power.
        catch_peach_idx.append(i + 1)

    if len(catch_indices) < 2:
        logger.debug('segment_strokes_from_peach_timestamps: fewer than 2 catches found')
        return []

    # Deduplicate: catches must be separated by at least 10 samples (200 ms at 50 Hz)
    deduped:    List[int]   = [catch_indices[0]]
    deduped_p:  List[float] = [peach_periods[0]]
    deduped_pi: List[int]   = [catch_peach_idx[0]]
    for ci, pp, pi in zip(catch_indices[1:], peach_periods[1:], catch_peach_idx[1:]):
        if ci - deduped[-1] > 10:
            deduped.append(ci)
            deduped_p.append(pp)
            deduped_pi.append(pi)
    catch_indices   = deduped
    peach_periods   = deduped_p
    catch_peach_idx = deduped_pi

    # ── Build StrokeInfo windows [catch_k, catch_{k+1}] ──────────────────────
    strokes: List[StrokeInfo] = []

    for i in range(len(catch_indices) - 1):
        i_start = catch_indices[i]
        i_end   = catch_indices[i + 1]

        if i_end - i_start < 5:
            continue

        # Filter inactive strokes
        max_force = 0.0
        for col in data_cols:
            if 'GateForceX' in col:
                arr = data[col][i_start:i_end + 1]
                if len(arr):
                    max_force = max(max_force, _safe_nanmax(np.abs(arr), default=0.0))
        if max_force < min_force_n:
            continue

        t_catch_start = float(time_ms[i_start])
        # FIX: the last stroke before a stop ran on to the first catch after it.
        # Cap the window at the device period plus 1 s.
        i_cap = int(np.searchsorted(time_ms, t_catch_start + peach_periods[i] + 1000.0, side='right')) - 1
        i_end = max(i_start + 5, min(i_end, i_cap))
        t_catch_end   = float(time_ms[i_end])
        # Use the Peach's own inter-stroke period for power accuracy
        period_ms = peach_periods[i]
        rate_spm  = 60000.0 / period_ms

        channels: Dict[str, np.ndarray] = {
            col: data[col][i_start:i_end + 1].copy() for col in data_cols
        }

        stroke = StrokeInfo(
            index=len(strokes),
            start_idx=i_start,
            end_idx=i_end,
            time_ms=t_catch_start,
            time_end_ms=t_catch_end,
            duration_ms=period_ms,
            rate_spm=rate_spm,
            channels=channels,
            peach_row_index=catch_peach_idx[i],   # which peach_rows entry for power lookup
        )
        strokes.append(stroke)

    logger.debug(
        f'segment_strokes_from_peach_timestamps: {len(strokes)} active strokes '
        f'from {len(peach_rows)} Peach rows ({len(catch_indices)} catches found)'
    )
    return strokes


def _segment_strokes_by_time(
    data: Dict[str, np.ndarray],
    peach_rows: List[Dict],
    min_force_n: float = 5.0,
) -> List[StrokeInfo]:
    """Fallback: use Peach timestamps directly as window boundaries (no angle data)."""
    time_ms   = data['Time'].astype(np.float64)
    n_total   = len(time_ms)
    data_cols = [c for c in data if c != 'Time']
    strokes: List[StrokeInfo] = []

    for i in range(len(peach_rows) - 1):
        t_start   = float(peach_rows[i]['time'])
        t_end     = float(peach_rows[i + 1]['time'])
        period_ms = t_end - t_start
        if period_ms <= 0:
            continue

        ia = int(np.searchsorted(time_ms, t_start, side='left'))
        ib = int(np.searchsorted(time_ms, t_end,   side='left')) - 1
        ib = min(max(ia, ib), n_total - 1)
        if ib - ia < 4:
            continue

        max_force = 0.0
        for col in data_cols:
            if 'GateForceX' in col:
                arr = data[col][ia:ib + 1]
                if len(arr):
                    max_force = max(max_force, _safe_nanmax(np.abs(arr), default=0.0))
        if max_force < min_force_n:
            continue

        channels = {col: data[col][ia:ib + 1].copy() for col in data_cols}
        strokes.append(StrokeInfo(
            index=len(strokes), start_idx=ia, end_idx=ib,
            time_ms=t_start, time_end_ms=t_end,
            duration_ms=period_ms, rate_spm=60000.0 / period_ms,
            channels=channels, peach_row_index=i + 1,
        ))

    return strokes


# ── Resampling ─────────────────────────────────────────────────────────────────

def resample(values: np.ndarray, n_points: int = N_RESAMPLE) -> np.ndarray:
    """Resample a variable-length array to n_points using linear interpolation."""
    n = len(values)
    if n < 5:
        return np.full(n_points, np.nan)
    return np.interp(np.linspace(0, 1, n_points), np.linspace(0, 1, n), values)


def _compute_mean_std(arrays: List[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Compute element-wise mean and std from a list of same-length arrays."""
    if not arrays:
        return np.full(N_RESAMPLE, np.nan), np.full(N_RESAMPLE, np.nan)
    mat = np.array(arrays, dtype=np.float64)
    finite = np.isfinite(mat)
    counts = finite.sum(axis=0)
    sums = np.where(finite, mat, 0.0).sum(axis=0)

    mean = np.full(mat.shape[1], np.nan, dtype=np.float64)
    valid = counts > 0
    mean[valid] = sums[valid] / counts[valid]

    std = np.full(mat.shape[1], np.nan, dtype=np.float64)
    if np.any(valid):
        diffs = np.where(finite, mat - mean, 0.0)
        std[valid] = np.sqrt((diffs * diffs).sum(axis=0)[valid] / counts[valid])
    return mean, std


def _circular_shift(arr: np.ndarray, shift: int) -> np.ndarray:
    """Circular shift for time-based view (catch at 0%)."""
    return np.roll(arr, shift)


# ── Average Stroke Computation ─────────────────────────────────────────────────

def compute_rower_curves(strokes: List[StrokeInfo], seat: int,
                         n_points: int = N_RESAMPLE,
                         side_prefix: str = '') -> Optional[RowerCurves]:
    """Compute average stroke curves for a single rower.

    Args:
        side_prefix: '' for port/sweep, 'S_' for strokeside (sculling).
    """
    angle_key = f'{side_prefix}GateAngle_{seat}'
    forcex_key = f'{side_prefix}GateForceX_{seat}'
    forcey_key = f'{side_prefix}GateForceY_{seat}'
    anglevel_key = f'{side_prefix}GateAngleVel_{seat}'
    seatpos_key = f'SeatPosn_{seat}' if not side_prefix else None
    seatposvel_key = f'SeatPosnVel_{seat}' if not side_prefix else None
    stretcher_keys = {
        'stretcherforcex': f'StretcherForceX_{seat}',
        'stretcherz': f'StretcherZ_{seat}',
        'stretcherrl': f'StretcherRL_{seat}',
        'stretchertb': f'StretcherTB_{seat}',
    } if not side_prefix else {}

    if not strokes or angle_key not in strokes[0].channels:
        return None

    shift = n_points // 4  # Seam at 25% (recovery phase, low-force region)

    # Resample all strokes
    angles_raw = []
    forcex_raw = []
    forcey_raw = []
    anglevel_raw = []
    seatpos_raw = []
    seatposvel_raw = []
    stretcher_raw: Dict[str, List[np.ndarray]] = {key: [] for key in stretcher_keys}
    has_forcey = forcey_key in strokes[0].channels
    has_seatpos = bool(seatpos_key and seatpos_key in strokes[0].channels)
    has_seatposvel = bool(seatposvel_key and seatposvel_key in strokes[0].channels)
    has_stretcher = {
        key: col_key in strokes[0].channels
        for key, col_key in stretcher_keys.items()
    }

    def _smooth3(a: np.ndarray) -> np.ndarray:
        """3-point moving average to suppress quantisation noise in anglevel."""
        if len(a) < 3:
            return a
        return np.convolve(a, np.ones(3) / 3.0, mode='same')

    for s in strokes:
        angles_raw.append(resample(s.channels[angle_key], n_points))
        forcex_raw.append(resample(s.channels.get(forcex_key, np.array([])), n_points))
        if has_forcey:
            forcey_raw.append(resample(s.channels.get(forcey_key, np.array([])), n_points))
        # Smooth anglevel before resampling to remove high-frequency quantisation
        # artefacts that create spurious secondary loops ("teeth") in the
        # GateAngleVel vs GateAngle parametric chart near the finish zone.
        raw_av = s.channels.get(anglevel_key, np.array([]))
        if len(raw_av) >= 3:
            raw_av = _smooth3(np.asarray(raw_av, dtype=float))
        anglevel_raw.append(resample(raw_av, n_points))
        if has_seatpos and seatpos_key is not None:
            seatpos_raw.append(resample(s.channels.get(seatpos_key, np.array([])), n_points))
        if has_seatposvel and seatposvel_key is not None:
            seatposvel_raw.append(resample(s.channels.get(seatposvel_key, np.array([])), n_points))
        for key, col_key in stretcher_keys.items():
            if has_stretcher.get(key):
                stretcher_raw[key].append(resample(s.channels.get(col_key, np.array([])), n_points))

    # Force is already in Newtons (binary reader SCALE_FORCE=0.5)
    forcex_raw_n = forcex_raw

    # Raw (parametric) means, force already in N
    angle_raw_mean, _ = _compute_mean_std(angles_raw)
    forcex_raw_mean, forcex_raw_std = _compute_mean_std(forcex_raw_n)
    seatpos_raw_mean = seatpos_raw_std = None
    if has_seatpos and seatpos_raw:
        seatpos_raw_mean, seatpos_raw_std = _compute_mean_std(seatpos_raw)
    seatposvel_raw_mean = seatposvel_raw_std = None
    if has_seatposvel and seatposvel_raw:
        seatposvel_raw_mean, seatposvel_raw_std = _compute_mean_std(seatposvel_raw)
    stretcher_raw_stats = {}
    for key, arrays in stretcher_raw.items():
        if has_stretcher.get(key) and arrays:
            stretcher_raw_stats[key] = _compute_mean_std(arrays)
        else:
            stretcher_raw_stats[key] = (None, None)

    # Time-based (shifted) means
    angles_shifted   = [_circular_shift(a, shift) for a in angles_raw]
    forcex_shifted_n = [_circular_shift(f, shift) for f in forcex_raw_n]
    anglevel_shifted = [_circular_shift(a, shift) for a in anglevel_raw]
    seatpos_shifted = [_circular_shift(a, shift) for a in seatpos_raw]
    seatposvel_shifted = [_circular_shift(a, shift) for a in seatposvel_raw]
    stretcher_shifted = {
        key: [_circular_shift(a, shift) for a in arrays]
        for key, arrays in stretcher_raw.items()
    }

    angle_mean,   angle_std   = _compute_mean_std(angles_shifted)
    forcex_mean,  forcex_std  = _compute_mean_std(forcex_shifted_n)
    anglevel_mean, anglevel_std = _compute_mean_std(anglevel_shifted)
    seatpos_mean = seatpos_std = None
    if has_seatpos and seatpos_shifted:
        seatpos_mean, seatpos_std = _compute_mean_std(seatpos_shifted)
    seatposvel_mean = seatposvel_std = None
    if has_seatposvel and seatposvel_shifted:
        seatposvel_mean, seatposvel_std = _compute_mean_std(seatposvel_shifted)
    stretcher_stats = {}
    for key, arrays in stretcher_shifted.items():
        if has_stretcher.get(key) and arrays:
            stretcher_stats[key] = _compute_mean_std(arrays)
        else:
            stretcher_stats[key] = (None, None)

    forcey_mean = forcey_std = None
    if has_forcey and forcey_raw:
        forcey_raw_n = forcey_raw  # already in Newtons
        forcey_shifted = [_circular_shift(f, shift) for f in forcey_raw_n]
        forcey_mean, forcey_std = _compute_mean_std(forcey_shifted)
    for key in ('stretcherforcex', 'stretcherz', 'stretcherrl', 'stretchertb'):
        stretcher_stats.setdefault(key, (None, None))
        stretcher_raw_stats.setdefault(key, (None, None))

    return RowerCurves(
        seat=seat,
        n_strokes=len(strokes),
        angle_mean=angle_mean,
        angle_std=angle_std,
        forcex_mean=forcex_mean,
        forcex_std=forcex_std,
        forcey_mean=forcey_mean,
        forcey_std=forcey_std,
        anglevel_mean=anglevel_mean,
        anglevel_std=anglevel_std,
        angle_raw_mean=angle_raw_mean,
        forcex_raw_mean=forcex_raw_mean,
        forcex_raw_std=forcex_raw_std,
        seatpos_mean=seatpos_mean,
        seatpos_std=seatpos_std,
        seatposvel_mean=seatposvel_mean,
        seatposvel_std=seatposvel_std,
        seatpos_raw_mean=seatpos_raw_mean,
        seatpos_raw_std=seatpos_raw_std,
        seatposvel_raw_mean=seatposvel_raw_mean,
        seatposvel_raw_std=seatposvel_raw_std,
        stretcherforcex_mean=stretcher_stats['stretcherforcex'][0],
        stretcherforcex_std=stretcher_stats['stretcherforcex'][1],
        stretcherz_mean=stretcher_stats['stretcherz'][0],
        stretcherz_std=stretcher_stats['stretcherz'][1],
        stretcherrl_mean=stretcher_stats['stretcherrl'][0],
        stretcherrl_std=stretcher_stats['stretcherrl'][1],
        stretchertb_mean=stretcher_stats['stretchertb'][0],
        stretchertb_std=stretcher_stats['stretchertb'][1],
        stretcherforcex_raw_mean=stretcher_raw_stats['stretcherforcex'][0],
        stretcherforcex_raw_std=stretcher_raw_stats['stretcherforcex'][1],
        stretcherz_raw_mean=stretcher_raw_stats['stretcherz'][0],
        stretcherz_raw_std=stretcher_raw_stats['stretcherz'][1],
        stretcherrl_raw_mean=stretcher_raw_stats['stretcherrl'][0],
        stretcherrl_raw_std=stretcher_raw_stats['stretcherrl'][1],
        stretchertb_raw_mean=stretcher_raw_stats['stretchertb'][0],
        stretchertb_raw_std=stretcher_raw_stats['stretchertb'][1],
        strokes_angle_raw=angles_raw,
        strokes_forcex_raw=forcex_raw,
    )


def compute_boat_curves(strokes: List[StrokeInfo],
                        n_points: int = N_RESAMPLE) -> BoatCurves:
    """Compute average per-stroke boat telemetry."""
    shift = n_points // 4  # Match rower curves: seam at 25%
    channels = {
        'speed': 'Speed_Boat', 'accel': 'Accel_Boat',
        'roll': 'Roll_Boat', 'pitch': 'Pitch_Boat', 'yaw': 'Yaw_Boat',
    }

    results = {}
    for short_name, col_name in channels.items():
        raw = []
        for s in strokes:
            if col_name in s.channels:
                raw.append(resample(s.channels[col_name], n_points))

        if raw:
            shifted = [_circular_shift(a, shift) for a in raw]
            mean, std = _compute_mean_std(shifted)
        else:
            mean = np.full(n_points, np.nan)
            std = np.full(n_points, np.nan)
        results[f'{short_name}_mean'] = mean
        results[f'{short_name}_std'] = std

    return BoatCurves(
        n_strokes=len(strokes),
        speed_mean=results['speed_mean'], speed_std=results['speed_std'],
        accel_mean=results['accel_mean'], accel_std=results['accel_std'],
        roll_mean=results['roll_mean'], roll_std=results['roll_std'],
        pitch_mean=results['pitch_mean'], pitch_std=results['pitch_std'],
        yaw_mean=results['yaw_mean'], yaw_std=results['yaw_std'],
    )


def extract_all_stroke_curves(strokes: List[StrokeInfo],
                              crew_size: int,
                              is_sculling: bool,
                              n_points: int = N_RESAMPLE) -> List[Dict]:
    """Extract per-stroke resampled curves for all strokes.

    Returns list of dicts ready for Parquet serialization:
        {stroke_index, seat, side, channel, curve_type, values}

    Where values is a list of n_points floats (resampled curve).

    Args:
        strokes: List of StrokeInfo from a session segment
        crew_size: Number of rowers
        is_sculling: True if sculling
        n_points: Resample target (default 200)

    Returns: List of row dicts for Parquet
    """
    rows = []
    shift = n_points // 4  # Match existing rower_curves: seam at 25%

    for s_idx, stroke in enumerate(strokes):
        # Rower curves (port/sweep)
        for seat in range(1, crew_size + 1):
            angle_key = f'GateAngle_{seat}'
            if angle_key not in stroke.channels:
                continue

            side = 'port' if is_sculling else 'sweep'

            # Time-based channels (shifted)
            for channel, col_key, std_col in [
                ('angle', f'GateAngle_{seat}', None),
                ('forcex', f'GateForceX_{seat}', None),
                ('forcey', f'GateForceY_{seat}', None),
                ('anglevel', f'GateAngleVel_{seat}', None),
                ('seatpos', f'SeatPosn_{seat}', None),
                ('seatposvel', f'SeatPosnVel_{seat}', None),
                ('stretcherforcex', f'StretcherForceX_{seat}', None),
                ('stretcherz', f'StretcherZ_{seat}', None),
                ('stretcherrl', f'StretcherRL_{seat}', None),
                ('stretchertb', f'StretcherTB_{seat}', None),
            ]:
                if col_key not in stroke.channels:
                    continue

                resampled = resample(stroke.channels[col_key], n_points)
                shifted = _circular_shift(resampled, shift)

                rows.append({
                    'stroke_index': s_idx,
                    'seat': seat,
                    'side': side,
                    'channel': channel,
                    'curve_type': 'time_based',
                    'values': shifted.astype(np.float32).tolist(),
                })

            # Parametric curves (raw, unshifted)
            for channel, col_key in [
                ('angle_raw', f'GateAngle_{seat}'),
                ('forcex_raw', f'GateForceX_{seat}'),
                ('anglevel_raw', f'GateAngleVel_{seat}'),
                ('seatpos_raw', f'SeatPosn_{seat}'),
                ('seatposvel_raw', f'SeatPosnVel_{seat}'),
                ('stretcherforcex_raw', f'StretcherForceX_{seat}'),
                ('stretcherz_raw', f'StretcherZ_{seat}'),
                ('stretcherrl_raw', f'StretcherRL_{seat}'),
                ('stretchertb_raw', f'StretcherTB_{seat}'),
            ]:
                if col_key not in stroke.channels:
                    continue

                raw = stroke.channels[col_key]
                # Smooth anglevel to suppress quantisation noise spikes
                if channel == 'anglevel_raw' and len(raw) >= 3:
                    raw = np.convolve(np.asarray(raw, dtype=float),
                                      np.ones(3) / 3.0, mode='same')
                resampled = resample(raw, n_points)
                rows.append({
                    'stroke_index': s_idx,
                    'seat': seat,
                    'side': side,
                    'channel': channel,
                    'curve_type': 'parametric_raw',
                    'values': resampled.astype(np.float32).tolist(),
                })

        # Sculling strokeside curves
        if is_sculling:
            for seat in range(1, crew_size + 1):
                angle_key = f'S_GateAngle_{seat}'
                if angle_key not in stroke.channels:
                    continue

                side = 'starboard'

                # Time-based
                for channel, col_key in [
                    ('angle', f'S_GateAngle_{seat}'),
                    ('forcex', f'S_GateForceX_{seat}'),
                    ('forcey', f'S_GateForceY_{seat}'),
                    ('anglevel', f'S_GateAngleVel_{seat}'),
                ]:
                    if col_key not in stroke.channels:
                        continue

                    resampled = resample(stroke.channels[col_key], n_points)
                    shifted = _circular_shift(resampled, shift)

                    rows.append({
                        'stroke_index': s_idx,
                        'seat': seat,
                        'side': side,
                        'channel': channel,
                        'curve_type': 'time_based',
                        'values': shifted.astype(np.float32).tolist(),
                    })

                # Parametric
                for channel, col_key in [
                    ('angle_raw', f'S_GateAngle_{seat}'),
                    ('forcex_raw', f'S_GateForceX_{seat}'),
                    ('anglevel_raw', f'S_GateAngleVel_{seat}'),
                ]:
                    if col_key not in stroke.channels:
                        continue

                    raw = stroke.channels[col_key]
                    if channel == 'anglevel_raw' and len(raw) >= 3:
                        raw = np.convolve(np.asarray(raw, dtype=float),
                                          np.ones(3) / 3.0, mode='same')
                    resampled = resample(raw, n_points)
                    rows.append({
                        'stroke_index': s_idx,
                        'seat': seat,
                        'side': side,
                        'channel': channel,
                        'curve_type': 'parametric_raw',
                        'values': resampled.astype(np.float32).tolist(),
                    })

        # Boat channels
        for channel, col_name in [
            ('speed', 'Speed_Boat'),
            ('accel', 'Accel_Boat'),
            ('roll', 'Roll_Boat'),
            ('pitch', 'Pitch_Boat'),
            ('yaw', 'Yaw_Boat'),
        ]:
            if col_name not in stroke.channels:
                continue

            resampled = resample(stroke.channels[col_name], n_points)
            shifted = _circular_shift(resampled, shift)

            rows.append({
                'stroke_index': s_idx,
                'seat': 0,
                'side': 'boat',
                'channel': channel,
                'curve_type': 'time_based',
                'values': shifted.astype(np.float32).tolist(),
            })

    return rows


# ── xlsx aperiodic helpers ──────────────────────────────────────────────────────

def _find_per_stroke_sheet(wb) -> Optional[object]:
    """
    Find the sheet in a Peach xlsx workbook that contains per-stroke averages.

    Peach xlsx sheet naming varies across firmware versions:
      - 2/4/8 exports:  'aperiodic' sheet holds per-stroke data
      - 1x exports:     'Periodic' sheet holds per-stroke data (capitalized,
                        confusingly named, it means one value per stroke cycle)

    We pick the sheet whose first header row contains at least one column that
    maps to a known per-stroke metric (SwivelPower, MinAngle, Drive Time, etc.).
    Falls back to any sheet named 'aperiodic'/'Aperiodic' if no match found.
    """
    _known = {k.strip().lower() for k in XLSX_AP_METRIC_MAP}
    def _sheet_has_metric_cols(ws) -> bool:
        rows = ws.iter_rows(min_row=1, max_row=1, values_only=True)
        hdrs = next(rows, None)
        if not hdrs:
            return False
        for h in hdrs:
            if h is None:
                continue
            raw = str(h).strip()
            low = raw.lower()
            # Direct match
            if low in _known:
                return True
            # Strip leading side prefix: 'p ', 's ', 'p. ', 's. '
            stripped = low
            for prefix in ('p ', 's ', 'p. ', 's. '):
                if stripped.startswith(prefix):
                    stripped = stripped[len(prefix):]
                    break
            if stripped in _known:
                return True
        return False

    # Search all sheets, prefer the one with the most metric hits
    best_sheet = None
    for name in wb.sheetnames:
        ws = wb[name]
        if _sheet_has_metric_cols(ws):
            best_sheet = ws
            break  # First matching sheet wins (typically 'aperiodic' or 'Periodic')

    if best_sheet is None:
        # Fallback: case-insensitive 'aperiodic' name
        for name in wb.sheetnames:
            if name.lower() == 'aperiodic':
                best_sheet = wb[name]
                break

    return best_sheet


def parse_xlsx_aperiodic(xlsx_path: str) -> Optional[Dict[float, Dict[str, float]]]:
    """
    Parse the per-stroke averages sheet from a Peach xlsx export.

    Automatically detects the correct sheet (handles naming differences across
    Peach firmware versions, some use 'aperiodic', some use 'Periodic').

    Returns dict: {time_ms → {field_seat_key: value}}
    e.g. {172720.0: {'swivel_power_1': 110.0, 'catch_angle_1': -46.125, ...}}
    Returns None if file not found or no recognisable per-stroke sheet.
    """
    try:
        import openpyxl
        wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
        ws = _find_per_stroke_sheet(wb)
        if ws is None:
            wb.close()
            logger.warning(f'xlsx aperiodic: no per-stroke sheet found in {xlsx_path}')
            return None
        all_rows = list(ws.iter_rows(values_only=True))
        wb.close()
    except Exception as exc:
        logger.warning(f'xlsx aperiodic: could not open {xlsx_path}: {exc}')
        return None

    if len(all_rows) < 3:
        return None

    # Row 0 = metric names, Row 1 = seat numbers
    header_metric = all_rows[0]
    header_seat   = all_rows[1]

    # Build a normalised lookup: strip + lowercase key → (field, scale)
    _norm_map: Dict[str, tuple] = {
        k.strip().lower(): v for k, v in XLSX_AP_METRIC_MAP.items()
    }
    _unmatched: set = set()

    # Build column descriptor: list of (field, seat_or_None, scale) or None
    col_desc: list = []
    for metric, seat_raw in zip(header_metric, header_seat):
        if metric is None:
            col_desc.append(None)
            continue
        metric_str = str(metric).strip()
        # Strip leading side prefix used by 1x exports: 'P ', 'S ', 'P. ', 'S. '
        for _pfx in ('P. ', 'S. ', 'P ', 'S '):
            if metric_str.startswith(_pfx):
                metric_str = metric_str[len(_pfx):]
                break
        metric_key = metric_str.lower()
        if metric_key in _norm_map:
            field, scale = _norm_map[metric_key]
            try:
                seat = int(seat_raw)
            except (TypeError, ValueError):
                seat = None
            col_desc.append((field, seat, scale))
        else:
            if metric_str not in _unmatched:
                _unmatched.add(metric_str)
                logger.debug(f'xlsx aperiodic: unrecognised column "{metric_str}"')
            col_desc.append(None)

    result: Dict[float, Dict[str, float]] = {}
    for row in all_rows[2:]:
        if not row or row[0] is None:
            continue
        try:
            t_ms = float(row[0])
        except (TypeError, ValueError):
            continue
        row_data: Dict[str, float] = {}
        for i, cell in enumerate(row):
            if i == 0 or i >= len(col_desc) or col_desc[i] is None or cell is None:
                continue
            field, seat, scale = col_desc[i]
            try:
                v = float(cell) * scale
            except (TypeError, ValueError):
                continue
            key = f'{field}_{seat}' if seat is not None else field
            row_data[key] = v
        if row_data:
            result[t_ms] = row_data

    logger.info(f'xlsx aperiodic: loaded {len(result)} stroke rows from {xlsx_path}')
    return result if result else None


def _match_xlsx_to_strokes(
    strokes: List[StrokeInfo],
    xlsx_ap: Dict[float, Dict[str, float]],
    max_gap_ms: float = 1800.0,
) -> List[Optional[Dict[str, float]]]:
    """
    One-to-one match: each xlsx per-stroke row is assigned to at most one
    binary stroke, and each binary stroke receives at most one xlsx row.

    Algorithm (greedy by proximity):
      For each xlsx row (in time order) find the nearest yet-unmatched binary
      stroke within max_gap_ms.  This prevents the old many-to-one problem
      where several early binary strokes (noise / warmup) all grabbed the same
      xlsx row, or where two adjacent binary strokes shared one xlsx entry.

    max_gap_ms = 1800 ms: the Peach device's catch-detection timestamp can
    differ from our binary minimum-angle detection by up to ~1500 ms at low
    stroke rates (≤22 SPM, period ≥2727 ms), because the Peach logs the
    stroke at a slightly different phase than our GateAngle local-minimum
    algorithm.  1800 ms comfortably covers this while staying below the
    half-period of the slowest practical rowing rate (~20 SPM → 3000 ms).
    The one-to-one constraint prevents false matches even with this wider
    window.

    Returns list indexed by binary stroke index: dict of xlsx values or None.
    """
    if not strokes or not xlsx_ap:
        return [None] * len(strokes)

    stroke_times = np.array([s.time_ms for s in strokes], dtype=np.float64)
    xlsx_times   = sorted(xlsx_ap.keys())

    matched: List[Optional[Dict[str, float]]] = [None] * len(strokes)
    used: set = set()   # binary stroke indices already claimed

    for t_xlsx in xlsx_times:
        # Binary search for nearest binary stroke
        idx = int(np.searchsorted(stroke_times, t_xlsx))

        # Consider the two candidates around the insertion point
        best_ci, best_gap = None, float('inf')
        for ci in (idx - 1, idx):
            if 0 <= ci < len(strokes) and ci not in used:
                gap = abs(stroke_times[ci] - t_xlsx)
                if gap <= max_gap_ms and gap < best_gap:
                    best_gap = gap
                    best_ci = ci

        if best_ci is None:
            continue   # no eligible binary stroke for this xlsx row

        used.add(best_ci)
        # Merge with any existing data (shouldn't happen with one-to-one,
        # but guards against duplicate xlsx timestamps).
        existing = matched[best_ci]
        if existing is None:
            matched[best_ci] = dict(xlsx_ap[t_xlsx])
        else:
            existing.update(xlsx_ap[t_xlsx])

    n_matched = sum(1 for m in matched if m is not None)
    logger.debug(
        f'xlsx match: {n_matched}/{len(strokes)} binary strokes matched '
        f'to {len(xlsx_times)} xlsx rows (max_gap={max_gap_ms:.0f}ms)'
    )
    return matched


def compute_periodic_summary(strokes: List[StrokeInfo], crew_size: int,
                              xlsx_ap: Optional[Dict] = None,
                              peach_rows: Optional[List[Dict]] = None,
                              slip_threshold_kgf: float = SLIP_THRESHOLD_KGF,
                              wash_threshold_kgf: float = WASH_THRESHOLD_KGF) -> PeriodicSummary:
    """Extract per-stroke periodic summary from segmented strokes."""
    n = len(strokes)
    summary = PeriodicSummary(
        stroke_index=np.arange(n),
        time_ms=np.array([s.time_ms for s in strokes]),
        rate_spm=np.array([s.rate_spm for s in strokes]),
        duration_ms=np.array([s.duration_ms for s in strokes]),
    )

    # Per-rower peak force
    for seat in range(1, crew_size + 1):
        col = f'GateForceX_{seat}'
        peaks = []
        for s in strokes:
            if col in s.channels:
                peaks.append(_safe_nanmax(s.channels[col]))
            else:
                peaks.append(float('nan'))
        summary.peak_force[seat] = np.array(peaks)

    # Boat metrics per stroke
    speed_avgs = []
    for s in strokes:
        if 'Speed_Boat' in s.channels and len(s.channels['Speed_Boat']) > 0 and _has_finite(s.channels['Speed_Boat']):
            speed_avgs.append(_safe_nanmean(s.channels['Speed_Boat'], default=0.0))
        else:
            speed_avgs.append(0.0)
    summary.avg_speed = np.array(speed_avgs)

    # Per-rower aperiodic per-stroke metrics (named as in PowerLine's Excel export)
    for seat in range(1, crew_size + 1):
        angle_col = f'GateAngle_{seat}'
        force_col = f'GateForceX_{seat}'
        anglevel_col = f'GateAngleVel_{seat}'

        _NAN = float('nan')
        cols = {k: [] for k in (
            'catch', 'finish', 'slip', 'wash', 'arc', 'eff_arc',
            'drive_start', 'drive_time', 'rec_time', 'ratio',
            'ang_max_f', 'ang_70f',
            'work', 'swivel_pwr',
            'wq1', 'wq2', 'wq3', 'wq4',
        )}

        def _nan_all(target_len: int):
            for lst in cols.values():
                while len(lst) < target_len:
                    lst.append(_NAN)

        for s in strokes:
            target_len = len(cols['catch']) + 1
            if angle_col not in s.channels or force_col not in s.channels:
                _nan_all(target_len); continue

            angle  = s.channels[angle_col].astype(np.float64)
            force  = s.channels[force_col].astype(np.float64)
            omega  = s.channels.get(anglevel_col, np.zeros_like(angle)).astype(np.float64)
            nn = len(angle)

            if nn < 2 or not np.isfinite(angle[0]) or not _has_finite(angle) or not _has_finite(force):
                _nan_all(target_len); continue

            # Sample-level time array (ms from stroke start)
            t_ms = np.linspace(0.0, s.duration_ms, nn)

            # ── MinAngle / MaxAngle ──────────────────────────────────────
            # Catch angle = angle at index 0 (the catch IS the stroke start).
            # Do NOT use nanmin() over the full window, the window ends at the
            # NEXT catch, which can be deeper and would give the wrong value.
            # MaxAngle (finish) remains nanmax() since the finish is always in
            # the interior of the window, well before the next catch.
            min_angle = float(angle[0])
            max_angle = _safe_nanmax(angle)
            cols['catch'].append(min_angle)
            cols['finish'].append(max_angle)
            arc = max_angle - min_angle
            cols['arc'].append(arc)

            # ── Load window: contiguous region centered on peak ──────────
            # Walk backward/forward from the force peak until force drops
            # below threshold.  Avoids including recovery-phase samples that
            # may have baseline force above a simple global threshold.
            peak_idx_inner = _safe_nanargmax(force)
            if peak_idx_inner is None:
                _nan_all(target_len); continue
            peak_f_kgf = float(force[peak_idx_inner])
            if not np.isfinite(peak_f_kgf):
                _nan_all(target_len); continue
            # force values are in Newtons (SCALE_FORCE=0.5 in binary reader); use FORCE_THRESHOLD_N
            dyn_thresh = max(FORCE_THRESHOLD_N, FORCE_THRESHOLD_PCT * peak_f_kgf)
            load_start = peak_idx_inner
            while load_start > 0 and force[load_start - 1] >= dyn_thresh:
                load_start -= 1
            load_end = peak_idx_inner
            while load_end < nn - 1 and force[load_end + 1] >= dyn_thresh:
                load_end += 1

            # ── CatchSlip / FinishSlip ───────────────────────────────────
            # Both are measured over the DRIVE PHASE only (catch → finish = max angle).
            # Restricting to force[:max_idx+1] prevents recovery-phase residual force
            # from pushing wash_above[-1] deep into recovery (which would inflate wash).
            #
            # Slip = angle swept from catch until force first reaches slip_threshold_n.
            # Wash = angle swept after force last drops below wash_threshold_n to finish.
            # Convert kgf thresholds to Newtons (force channel is in N, not kgf).
            #
            # INTERPOLATION: We linearly interpolate between the sample just before
            # threshold crossing and the sample just at/after crossing.  Sample-level
            # detection (without interpolation) introduces a systematic +2-4° error
            # at 50 Hz because each sample spans ~2-4° of oar travel at the catch/finish.
            # Interpolation brings values in line with Powerline's reference algorithm.
            slip_threshold_n = slip_threshold_kgf * KGF_TO_N   # e.g. 30 kgf = 294 N
            wash_threshold_n = wash_threshold_kgf * KGF_TO_N   # e.g. 15 kgf = 147 N
            max_idx = _safe_nanargmax(angle)
            if max_idx is None:
                _nan_all(target_len); continue
            drive_force = force[:max_idx + 1]   # drive phase only
            slip_above = np.where(drive_force >= slip_threshold_n)[0]
            if len(slip_above) > 0:
                first_above = slip_above[0]
                if first_above > 0:
                    # Interpolate: find the exact angle where force crosses slip_threshold_n
                    f0 = float(drive_force[first_above - 1])
                    f1 = float(drive_force[first_above])
                    a0 = float(angle[first_above - 1])
                    a1 = float(angle[first_above])
                    frac = (slip_threshold_n - f0) / (f1 - f0) if (f1 - f0) != 0 else 0.0
                    crossing_angle = a0 + frac * (a1 - a0)
                else:
                    crossing_angle = float(angle[first_above])
                slip = abs(crossing_angle - float(angle[0]))
            else:
                slip = 0.0   # force never reached threshold, treat as no slip
            wash_above = np.where(drive_force >= wash_threshold_n)[0]
            if len(wash_above) > 0:
                last_above = wash_above[-1]
                if last_above < len(drive_force) - 1:
                    # Interpolate: find the exact angle where force drops below wash_threshold_n
                    f0 = float(drive_force[last_above])
                    f1 = float(drive_force[last_above + 1])
                    a0 = float(angle[last_above])
                    a1 = float(angle[last_above + 1])
                    frac = (wash_threshold_n - f0) / (f1 - f0) if (f1 - f0) != 0 else 0.0
                    crossing_angle = a0 + frac * (a1 - a0)
                else:
                    crossing_angle = float(angle[last_above])
                wash = abs(float(angle[max_idx]) - crossing_angle)
            else:
                wash = 0.0   # force never reached threshold, treat as no wash
            cols['slip'].append(slip)
            cols['wash'].append(wash)
            cols['eff_arc'].append(max(0.0, arc - slip - wash))

            # ── Drive Start T / Drive Time / Recovery Time ───────────────
            drive_start_ms = float(t_ms[load_start])
            drive_time_ms  = float(t_ms[load_end] - t_ms[load_start])
            rec_time_ms    = float(s.duration_ms - t_ms[load_end])
            cols['drive_start'].append(drive_start_ms)
            cols['drive_time'].append(max(drive_time_ms, 0.0))
            cols['rec_time'].append(max(rec_time_ms, 0.0))
            total_t = drive_time_ms + rec_time_ms
            cols['ratio'].append(drive_time_ms / total_t if total_t > 0 else _NAN)

            # ── Angle Max F / Angle 0.7 F ────────────────────────────────
            peak_idx = _safe_nanargmax(force)
            if peak_idx is None:
                _nan_all(target_len); continue
            cols['ang_max_f'].append(float(angle[peak_idx]))
            peak_f = float(force[peak_idx])
            thresh_70 = 0.7 * peak_f
            above_70 = np.where(force >= thresh_70)[0]
            cols['ang_70f'].append(float(angle[above_70[0]]) if len(above_70) > 0 else _NAN)

            # ── Work (J) = ∫F[N]·d(θ_rad)·r  over drive window ──────────
            # GateForceX already in Newtons (SCALE_FORCE=0.5 in binary reader)
            drive_angle = angle[load_start:load_end + 1]
            drive_force_n = force[load_start:load_end + 1].astype(np.float64)   # already Newtons
            if len(drive_angle) > 1:
                work = float(np.abs(np.trapezoid(drive_force_n, np.deg2rad(drive_angle)))) * OAR_MOMENT_ARM_M
            else:
                work = 0.0
            cols['work'].append(work)

            # ── SwivelPower (W) = Work / full stroke duration ─────────────
            # Average power = energy delivered per stroke / stroke period.
            # Using mean(F×ω×arm) during the drive gives drive-phase mean power
            # (≈5× higher than the full-stroke average shown by the Peach device).
            # The correct "average power" metric is Work[J] / T_stroke[s].
            cols['swivel_pwr'].append(work / (s.duration_ms / 1000.0) if s.duration_ms > 0 else 0.0)

            # ── Work quartiles (% work in each quarter of drive arc) ─────
            # Use half-open intervals [q_i, q_{i+1}) so boundaries are not
            # double-counted, guaranteeing Q1+Q2+Q3+Q4 = 100%.
            if len(drive_angle) > 3 and work > 0:
                q_bounds = np.linspace(drive_angle[0], drive_angle[-1], 5)
                q_works = []
                for qi in range(4):
                    lo, hi = q_bounds[qi], q_bounds[qi + 1]
                    # Half-open [lo, hi) except last quartile which is [lo, hi]
                    if qi < 3:
                        mask = (drive_angle >= lo) & (drive_angle < hi)
                    else:
                        mask = (drive_angle >= lo) & (drive_angle <= hi)
                    if mask.sum() > 1:
                        qw = float(np.abs(np.trapezoid(drive_force_n[mask],
                                                   np.deg2rad(drive_angle[mask])))) * OAR_MOMENT_ARM_M
                    else:
                        qw = 0.0
                    q_works.append(qw)
                total_qw = sum(q_works) or 1.0
                pcts = [100.0 * qw / total_qw for qw in q_works]
            else:
                pcts = [_NAN] * 4
            cols['wq1'].append(pcts[0])
            cols['wq2'].append(pcts[1])
            cols['wq3'].append(pcts[2])
            cols['wq4'].append(pcts[3])

        summary.catch_angle[seat]      = np.array(cols['catch'])
        summary.finish_angle[seat]     = np.array(cols['finish'])
        summary.slip_angle[seat]       = np.array(cols['slip'])
        summary.wash_angle[seat]       = np.array(cols['wash'])
        summary.arc_length[seat]       = np.array(cols['arc'])
        summary.effective_arc[seat]    = np.array(cols['eff_arc'])
        summary.drive_start_ms[seat]   = np.array(cols['drive_start'])
        summary.drive_time_ms[seat]    = np.array(cols['drive_time'])
        summary.recovery_time_ms[seat] = np.array(cols['rec_time'])
        summary.ratio[seat]            = np.array(cols['ratio'])
        summary.angle_max_force[seat]  = np.array(cols['ang_max_f'])
        summary.angle_70pct_force[seat]= np.array(cols['ang_70f'])
        summary.work_j[seat]           = np.array(cols['work'])
        summary.swivel_power[seat]     = np.array(cols['swivel_pwr'])
        summary.work_q1[seat]          = np.array(cols['wq1'])
        summary.work_q2[seat]          = np.array(cols['wq2'])
        summary.work_q3[seat]          = np.array(cols['wq3'])
        summary.work_q4[seat]          = np.array(cols['wq4'])

    # ── Sculling starboard catch/finish angles per stroke ──────────────────
    # Auto-detect sculling from presence of S_GateAngle channels
    _has_stbd = strokes and any(k.startswith('S_GateAngle_') for k in strokes[0].channels)
    if _has_stbd:
        for seat in range(1, crew_size + 1):
            s_angle_col = f'S_GateAngle_{seat}'
            s_force_col = f'S_GateForceX_{seat}'
            s_catch_list: list = []
            s_finish_list: list = []
            s_slip_list: list = []
            s_wash_list: list = []
            s_eff_arc_list: list = []
            for s in strokes:
                if s_angle_col not in s.channels:
                    s_catch_list.append(float('nan'))
                    s_finish_list.append(float('nan'))
                    s_slip_list.append(float('nan'))
                    s_wash_list.append(float('nan'))
                    s_eff_arc_list.append(float('nan'))
                    continue
                angle = s.channels[s_angle_col].astype(np.float64)
                force = s.channels.get(s_force_col)
                if len(angle) < 2 or not np.isfinite(angle[0]) or not _has_finite(angle):
                    s_catch_list.append(float('nan'))
                    s_finish_list.append(float('nan'))
                    s_slip_list.append(float('nan'))
                    s_wash_list.append(float('nan'))
                    s_eff_arc_list.append(float('nan'))
                    continue
                catch = float(angle[0])
                finish = _safe_nanmax(angle)
                s_catch_list.append(catch)
                s_finish_list.append(finish)
                if force is None or not _has_finite(force):
                    s_slip_list.append(float('nan'))
                    s_wash_list.append(float('nan'))
                    s_eff_arc_list.append(float('nan'))
                    continue

                force = force.astype(np.float64)
                max_idx = _safe_nanargmax(angle)
                if max_idx is None:
                    s_slip_list.append(float('nan'))
                    s_wash_list.append(float('nan'))
                    s_eff_arc_list.append(float('nan'))
                    continue

                drive_force = force[:max_idx + 1]
                slip_threshold_n = slip_threshold_kgf * KGF_TO_N
                wash_threshold_n = wash_threshold_kgf * KGF_TO_N

                slip_above = np.where(drive_force >= slip_threshold_n)[0]
                if len(slip_above) > 0:
                    first_above = slip_above[0]
                    if first_above > 0:
                        f0 = float(drive_force[first_above - 1])
                        f1 = float(drive_force[first_above])
                        a0 = float(angle[first_above - 1])
                        a1 = float(angle[first_above])
                        frac = (slip_threshold_n - f0) / (f1 - f0) if (f1 - f0) != 0 else 0.0
                        slip_crossing = a0 + frac * (a1 - a0)
                    else:
                        slip_crossing = float(angle[first_above])
                    slip = abs(slip_crossing - catch)
                else:
                    slip = 0.0

                wash_above = np.where(drive_force >= wash_threshold_n)[0]
                if len(wash_above) > 0:
                    last_above = wash_above[-1]
                    if last_above < len(drive_force) - 1:
                        f0 = float(drive_force[last_above])
                        f1 = float(drive_force[last_above + 1])
                        a0 = float(angle[last_above])
                        a1 = float(angle[last_above + 1])
                        frac = (wash_threshold_n - f0) / (f1 - f0) if (f1 - f0) != 0 else 0.0
                        wash_crossing = a0 + frac * (a1 - a0)
                    else:
                        wash_crossing = float(angle[last_above])
                    wash = abs(finish - wash_crossing)
                else:
                    wash = 0.0

                s_slip_list.append(slip)
                s_wash_list.append(wash)
                s_eff_arc_list.append(max(0.0, finish - catch - slip - wash))
            summary.catch_angle_stbd[seat] = np.array(s_catch_list)
            summary.finish_angle_stbd[seat] = np.array(s_finish_list)
            summary.slip_angle_stbd[seat] = np.array(s_slip_list)
            summary.wash_angle_stbd[seat] = np.array(s_wash_list)
            summary.effective_arc_stbd[seat] = np.array(s_eff_arc_list)

    # ── Override swivel_power with Peach binary periodic values ────────────
    # The Peach device computes per-seat power on-device with the correct algorithm.
    # When peach_rows are available (decode_sweep_rows / decode_scull_rows output),
    # use the device's own power values, these are always correct regardless of
    # any GateAngle encoding differences in the 50Hz aperiodic data.
    #
    # Matching: since strokes are now built with catch-finding (GateAngle minimum
    # within each Peach interval), stroke.time_ms is the 50Hz catch timestamp -
    # NOT the Peach row's finish timestamp.  We store peach_row_index on StrokeInfo
    # so we can do a direct index lookup instead of a time-based match.
    if peach_rows:
        # Fallback lookup by finish-timestamp for strokes without a peach_row_index
        _peach_by_time: Dict[float, Dict] = {float(r['time']): r for r in peach_rows}
        n_overridden = 0
        for si, s in enumerate(strokes):
            # Prefer direct index (set by segment_strokes_from_peach_timestamps)
            pri = s.peach_row_index
            if pri is not None and 0 <= pri < len(peach_rows):
                row = peach_rows[pri]
            else:
                # Fallback: time-based lookup (for strokes built by other segmenters)
                row = _peach_by_time.get(s.time_ms)
            if row is None:
                continue
            for seat in range(1, crew_size + 1):
                pwr_key = f'power_{seat}'
                if pwr_key in row:
                    arr = summary.swivel_power.get(seat)
                    if arr is not None and si < len(arr):
                        arr[si] = _not_negative(row[pwr_key])
                        n_overridden += 1
                # Sculling per-oar power, port (left) and stbd (right)
                lp_key = f'left_power_{seat}'
                rp_key = f'right_power_{seat}'
                if lp_key in row:
                    lp_arr = summary.left_power.get(seat)
                    if lp_arr is None:
                        lp_arr = np.full(len(strokes), np.nan)
                        summary.left_power[seat] = lp_arr
                    if si < len(lp_arr):
                        lp_arr[si] = _not_negative(row[lp_key])
                if rp_key in row:
                    rp_arr = summary.right_power.get(seat)
                    if rp_arr is None:
                        rp_arr = np.full(len(strokes), np.nan)
                        summary.right_power[seat] = rp_arr
                    if si < len(rp_arr):
                        rp_arr[si] = _not_negative(row[rp_key])
        logger.debug(
            f'compute_periodic_summary: overrode {n_overridden} seat-stroke power values '
            f'from Peach binary periodic rows ({len(strokes)} strokes)'
        )

    # ── Override with xlsx pre-computed values when available ───────────────
    # When a matching .xlsx file is present, its angle and timing metrics replace
    # the ones computed here. Power is NOT taken from Excel: where the device
    # reported no value, Excel repeats the previous stroke's power (see
    # docs/wrong-turns.md, 12). Device power from the file stays.
    if xlsx_ap:
        matched = _match_xlsx_to_strokes(strokes, xlsx_ap)
        # Per-seat fields to override (internal_name → summary attribute)
        seat_fields = [
            'catch_angle', 'slip_angle', 'finish_angle',
            'wash_angle', 'drive_start_ms', 'drive_time_ms', 'recovery_time_ms',
            'angle_max_force', 'angle_70pct_force',
            'work_q1', 'work_q2', 'work_q3', 'work_q4',
        ]
        for si, row in enumerate(matched):
            if row is None:
                continue
            for seat in range(1, len(summary.catch_angle) + 1):
                for field in seat_fields:
                    key = f'{field}_{seat}'
                    if key not in row:
                        continue
                    arr = getattr(summary, field, {}).get(seat)
                    if arr is not None and si < len(arr):
                        arr[si] = row[key]
            # Recompute arc_length and effective_arc from overridden values
            for seat in range(1, len(summary.catch_angle) + 1):
                ca = summary.catch_angle.get(seat)
                fa = summary.finish_angle.get(seat)
                sl = summary.slip_angle.get(seat)
                wa = summary.wash_angle.get(seat)
                if (ca is not None and fa is not None
                        and si < len(ca) and si < len(fa)):
                    arc = fa[si] - ca[si]
                    summary.arc_length[seat][si] = arc
                    if (sl is not None and wa is not None
                            and si < len(sl) and si < len(wa)):
                        summary.effective_arc[seat][si] = max(0.0, arc - sl[si] - wa[si])
            # Ratio from drive/recovery if not overridden directly
            for seat in range(1, len(summary.drive_time_ms) + 1):
                dt = summary.drive_time_ms.get(seat)
                rt = summary.recovery_time_ms.get(seat)
                ra = summary.ratio.get(seat)
                if dt is not None and rt is not None and ra is not None and si < len(dt):
                    total = dt[si] + rt[si]
                    ra[si] = dt[si] / total if total > 0 else float('nan')

    return summary


def compute_piece_averages(strokes: List[StrokeInfo], crew_size: int,
                           is_sculling: bool, avg_rate: float,
                           avg_speed: float,
                           periodic: Optional['PeriodicSummary'] = None,
                           slip_threshold_kgf: float = SLIP_THRESHOLD_KGF,
                           wash_threshold_kgf: float = WASH_THRESHOLD_KGF) -> PieceAverages:
    """Compute average metrics across all strokes for the piece summary table."""
    n = len(strokes)
    if n == 0:
        return PieceAverages(avg_rate=0, avg_speed=0, avg_distance_per_stroke=0,
                             duration_s=0, n_strokes=0)

    duration_s = sum(s.duration_ms for s in strokes) / 1000.0
    avg_dist = avg_speed * (duration_s / n) if n > 0 else 0.0

    avgs = PieceAverages(
        avg_rate=avg_rate,
        avg_speed=avg_speed,
        avg_distance_per_stroke=avg_dist,
        duration_s=duration_s,
        n_strokes=n,
    )

    for seat in range(1, crew_size + 1):
        # Port-side (or only side for sweep): ForceX power proxy
        fx_col = f'GateForceX_{seat}'
        angle_col = f'GateAngle_{seat}'
        # Strokeside (sculling only)
        sfx_col = f'S_GateForceX_{seat}'
        sangle_col = f'S_GateAngle_{seat}'

        # ── Helper: compute all aperiodic metrics for one stroke/side ───────
        def _stroke_metrics(s, a_col, fx_col_loc, omega_col):
            """Returns dict of per-stroke metrics or None if data missing."""
            if a_col not in s.channels or fx_col_loc not in s.channels:
                return None
            angle  = s.channels[a_col].astype(np.float64)
            force  = s.channels[fx_col_loc].astype(np.float64)
            omega  = s.channels.get(omega_col, np.zeros_like(angle)).astype(np.float64)
            nn = len(angle)
            if nn < 2 or not np.isfinite(angle[0]) or not _has_finite(angle) or not _has_finite(force):
                return None

            t_ms = np.linspace(0.0, s.duration_ms, nn)
            # Catch angle = angle at index 0 (stroke boundary = catch).
            # Do NOT use nanmin(), the window ends at the next catch which
            # can be deeper, yielding a wrong catch angle for the current stroke.
            min_angle = float(angle[0])
            max_angle = _safe_nanmax(angle)
            arc = max_angle - min_angle
            max_idx = _safe_nanargmax(angle)
            if max_idx is None:
                return None

            peak_idx = _safe_nanargmax(force)
            if peak_idx is None:
                return None
            peak_f_kgf = float(force[peak_idx])
            if not np.isfinite(peak_f_kgf):
                return None
            # force values are in Newtons (SCALE_FORCE=0.5 in binary reader); use FORCE_THRESHOLD_N
            dyn_thresh = max(FORCE_THRESHOLD_N, FORCE_THRESHOLD_PCT * peak_f_kgf)
            # Contiguous region from peak (handles baseline offset in recovery)
            load_start = peak_idx
            while load_start > 0 and force[load_start - 1] >= dyn_thresh:
                load_start -= 1
            load_end = peak_idx
            while load_end < nn - 1 and force[load_end + 1] >= dyn_thresh:
                load_end += 1

            # Slip / Wash restricted to drive phase [0:max_idx+1] to exclude
            # any residual force signal during recovery (which would inflate wash).
            # Convert kgf thresholds to Newtons (force channel is in N, not kgf).
            slip_threshold_n_pa = slip_threshold_kgf * KGF_TO_N   # e.g. 30 kgf = 294 N
            wash_threshold_n_pa = wash_threshold_kgf * KGF_TO_N   # e.g. 15 kgf = 147 N
            drive_force_sw = force[:max_idx + 1]
            slip_above_pa = np.where(drive_force_sw >= slip_threshold_n_pa)[0]
            slip = abs(float(angle[slip_above_pa[0]]) - float(angle[0])) if len(slip_above_pa) > 0 else 0.0
            wash_above_pa = np.where(drive_force_sw >= wash_threshold_n_pa)[0]
            wash = abs(float(angle[max_idx]) - float(angle[wash_above_pa[-1]])) if len(wash_above_pa) > 0 else 0.0
            eff  = max(0.0, arc - slip - wash)

            drive_start_ms = float(t_ms[load_start])
            drive_t_ms     = float(t_ms[load_end] - t_ms[load_start])
            rec_t_ms       = float(s.duration_ms - t_ms[load_end])
            total_t = drive_t_ms + rec_t_ms
            ratio  = drive_t_ms / total_t if total_t > 0 else float('nan')

            ang_maxf = float(angle[peak_idx])
            above_70 = np.where(force >= 0.7 * peak_f_kgf)[0]
            ang_70f  = float(angle[above_70[0]]) if len(above_70) > 0 else float('nan')

            da = angle[load_start:load_end + 1]
            df_n = force[load_start:load_end + 1].astype(np.float64)  # already in Newtons
            do = np.abs(omega[load_start:load_end + 1])
            work = (float(np.abs(np.trapezoid(df_n, np.deg2rad(da)))) * OAR_MOMENT_ARM_M
                    if len(da) > 1 else 0.0)
            # Average power = Work[J] / stroke_period[s]  (same as Peach device)
            swivel = work / (s.duration_ms / 1000.0) if s.duration_ms > 0 else 0.0

            if len(da) > 3 and work > 0:
                qb = np.linspace(da[0], da[-1], 5)
                q_works_m = []
                for qi in range(4):
                    lo_m, hi_m = qb[qi], qb[qi + 1]
                    if qi < 3:
                        mask = (da >= lo_m) & (da < hi_m)
                    else:
                        mask = (da >= lo_m) & (da <= hi_m)
                    qw = (float(np.abs(np.trapezoid(df_n[mask], np.deg2rad(da[mask])))) * OAR_MOMENT_ARM_M
                          if mask.sum() > 1 else 0.0)
                    q_works_m.append(qw)
                total_qw_m = sum(q_works_m) or 1.0
                pcts = [100.0 * qw / total_qw_m for qw in q_works_m]
            else:
                pcts = [float('nan')] * 4

            return dict(
                catch=min_angle, finish=max_angle, slip=slip, wash=wash,
                arc=arc, eff_arc=eff,
                drive_start=drive_start_ms, drive_time=max(drive_t_ms, 0.0),
                rec_time=max(rec_t_ms, 0.0), ratio=ratio,
                ang_maxf=ang_maxf, ang_70f=ang_70f,
                work=work, swivel=swivel,
                wq1=pcts[0], wq2=pcts[1], wq3=pcts[2], wq4=pcts[3],
            )

        def _nanmean_list(lst):
            v = [float(x) for x in lst if x is not None and np.isfinite(float(x))]
            return _safe_nanmean(np.array(v)) if v else None

        def _set(d, key, val):
            if val is not None and not (isinstance(val, float) and np.isnan(val)):
                d[seat] = val

        # Port / sweep side
        anglevel_col = f'GateAngleVel_{seat}'
        m_port = [_stroke_metrics(s, angle_col, fx_col, anglevel_col) for s in strokes]
        m_port = [m for m in m_port if m is not None]

        # Strokeside (sculling)
        sanglevel_col = f'S_GateAngleVel_{seat}'
        m_stbd = [_stroke_metrics(s, sangle_col, sfx_col, sanglevel_col) for s in strokes]
        m_stbd = [m for m in m_stbd if m is not None]

        # Peak force, already in Newtons (binary reader SCALE_FORCE=0.5)
        pf_vals = [
            _safe_nanmax(s.channels[fx_col])
            for s in strokes
            if fx_col in s.channels and _has_finite(s.channels[fx_col])
        ]
        if pf_vals:
            avgs.peak_force[seat] = _safe_nanmean(np.array(pf_vals))

        ar_vals = [
            _safe_nanmax(s.channels[angle_col]) - _safe_nanmin(s.channels[angle_col])
            for s in strokes
            if angle_col in s.channels and _has_finite(s.channels[angle_col])
        ]
        if ar_vals:
            avgs.avg_angle_range[seat] = _safe_nanmean(np.array(ar_vals))

        # Helper to nanmean a PeriodicSummary array (preferred source)
        def _avg_periodic(field_name):
            arr = getattr(periodic, field_name, {}).get(seat) if periodic else None
            if arr is None or len(arr) == 0:
                return None
            a = np.asarray(arr, dtype=float)
            valid = a[np.isfinite(a)]
            if len(valid) == 0:
                return None
            return float(valid.mean())

        if m_port or periodic:
            def avg(k):
                return _nanmean_list([m[k] for m in m_port]) if m_port else None

            def _prefer(periodic_name, binary_key):
                """Return periodic value if not None, else binary fallback.
                Uses explicit None check to avoid treating 0.0 as falsy."""
                v = _avg_periodic(periodic_name)
                return v if v is not None else avg(binary_key)

            avgs.avg_catch_angle[seat]      = _prefer('catch_angle',      'catch')
            avgs.avg_finish_angle[seat]     = _prefer('finish_angle',     'finish')
            avgs.avg_slip_angle[seat]       = _prefer('slip_angle',       'slip')
            avgs.avg_wash_angle[seat]       = _prefer('wash_angle',       'wash')
            avgs.avg_arc_length[seat]       = _prefer('arc_length',       'arc')
            avgs.avg_effective_arc[seat]    = _prefer('effective_arc',    'eff_arc')
            avgs.avg_drive_start_ms[seat]   = _prefer('drive_start_ms',   'drive_start')
            avgs.avg_drive_time_ms[seat]    = _prefer('drive_time_ms',    'drive_time')
            avgs.avg_recovery_time_ms[seat] = _prefer('recovery_time_ms', 'rec_time')
            avgs.avg_ratio[seat]            = _prefer('ratio',            'ratio')
            avgs.avg_angle_max_force[seat]  = _prefer('angle_max_force',  'ang_maxf')
            avgs.avg_angle_70pct_force[seat]= _prefer('angle_70pct_force','ang_70f')
            avgs.avg_work_j[seat]           = _prefer('work_j',           'work')
            avgs.avg_swivel_power[seat]     = _prefer('swivel_power',     'swivel')
            avgs.avg_work_q1[seat]          = _prefer('work_q1',          'wq1')
            avgs.avg_work_q2[seat]          = _prefer('work_q2',          'wq2')
            avgs.avg_work_q3[seat]          = _prefer('work_q3',          'wq3')
            avgs.avg_work_q4[seat]          = _prefer('work_q4',          'wq4')
            # avg_power = SwivelPower (main metric shown for rower power)
            avgs.avg_power[seat]            = avgs.avg_swivel_power.get(seat)

        if is_sculling:
            pf_s = [
                _safe_nanmax(s.channels[sfx_col])
                for s in strokes
                if sfx_col in s.channels and _has_finite(s.channels[sfx_col])
            ]
            ar_s = [
                _safe_nanmax(s.channels[sangle_col]) - _safe_nanmin(s.channels[sangle_col])
                for s in strokes
                if sangle_col in s.channels and _has_finite(s.channels[sangle_col])
            ]
            if pf_s:
                avgs.peak_force_stbd[seat]       = _safe_nanmean(np.array(pf_s))
                avgs.peak_force_port[seat]        = avgs.peak_force.get(seat, 0.0)
            if ar_s:
                avgs.avg_angle_range_stbd[seat]   = _safe_nanmean(np.array(ar_s))
                avgs.avg_angle_range_port[seat]   = avgs.avg_angle_range.get(seat, 0.0)
            if m_port:
                avgs.avg_catch_angle_port[seat]   = avgs.avg_catch_angle.get(seat, 0.0)
                avgs.avg_finish_angle_port[seat]  = avgs.avg_finish_angle.get(seat, 0.0)
                # Port power: prefer Peach device's own left_power (authoritative),
                # fall back to binary 50Hz trapz on port channels.
                port_pwr = _avg_periodic('left_power')
                if port_pwr is None:
                    port_pwr = _nanmean_list([m['swivel'] for m in m_port])
                avgs.avg_swivel_power_port[seat]  = port_pwr if port_pwr is not None else 0.0
                avgs.avg_power_port[seat]         = avgs.avg_swivel_power_port[seat]
            if m_stbd:
                def avgs_s(k): return _nanmean_list([m[k] for m in m_stbd])
                avgs.avg_catch_angle_stbd[seat]  = avgs_s('catch')
                avgs.avg_finish_angle_stbd[seat] = avgs_s('finish')
                # Stbd power: prefer Peach device's own right_power (authoritative),
                # fall back to binary 50Hz trapz on stbd channels.
                stbd_pwr = _avg_periodic('right_power')
                if stbd_pwr is None:
                    stbd_pwr = avgs_s('swivel')
                avgs.avg_swivel_power_stbd[seat]  = stbd_pwr if stbd_pwr is not None else 0.0
                avgs.avg_power_stbd[seat]         = avgs.avg_swivel_power_stbd[seat]

    return avgs


# ── Main Engine ────────────────────────────────────────────────────────────────

class TelemetryEngine:
    """
    Core telemetry processor for a single .peach-data session.

    Usage:
        engine = TelemetryEngine('session.peach-data')
        overview = engine.get_overview()       # fast, for the session chart
        piece = engine.analyze_piece(3000000, 3060000)  # selected segment
    """

    def __init__(self, peach_path: str, boat_type: str = '8',
                 rower_names: Optional[Dict[int, str]] = None,
                 force_offsets_n: Optional[Dict[str, float]] = None,
                 slip_threshold_kgf: float = SLIP_THRESHOLD_KGF,
                 wash_threshold_kgf: float = WASH_THRESHOLD_KGF,
                 sculling_slip_threshold_kgf: float = SCULLING_SLIP_THRESHOLD_KGF,
                 sculling_wash_threshold_kgf: float = SCULLING_WASH_THRESHOLD_KGF):
        self.path = Path(peach_path)
        self.boat_type = boat_type
        self.rower_names = rower_names or {}
        self.force_offsets_n = {
            str(k): float(v)
            for k, v in (force_offsets_n or {}).items()
            if v is not None
        }
        # Slip/wash thresholds (kgf), user-configurable via Settings → Telemetry
        # Separate values for sweep and sculling boats (different peak force ranges)
        self.slip_threshold_kgf = float(slip_threshold_kgf)
        self.wash_threshold_kgf = float(wash_threshold_kgf)
        self.sculling_slip_threshold_kgf = float(sculling_slip_threshold_kgf)
        self.sculling_wash_threshold_kgf = float(sculling_wash_threshold_kgf)
        self.reader = PeachBinaryReader(peach_path, boat_type=boat_type)
        self._last_overview: Optional[OverviewData] = None
        # Load per-stroke periodic rows from the binary (rowtools.strokes).
        # These provide device-computed timestamps and per-seat power values.
        self._periodic_crew: Optional[int] = None  # crew from periodic layout (may differ from aperiodic)
        self._peach_rows: Optional[List[Dict]] = self._load_peach_periodic_rows()
        # Load xlsx aperiodic data if available alongside the .peach-data file
        self._xlsx_ap: Optional[Dict] = self._load_xlsx_aperiodic()

    def _apply_force_offsets(
        self,
        data: Dict[str, np.ndarray],
        force_offsets_n: Optional[Dict[str, float]] = None,
    ) -> Dict[str, np.ndarray]:
        """Apply additive ForceX baseline corrections in Newtons."""
        offsets = self.force_offsets_n if force_offsets_n is None else {
            str(k): float(v)
            for k, v in (force_offsets_n or {}).items()
            if v is not None
        }
        if not offsets:
            return data
        for key, correction in offsets.items():
            if not np.isfinite(correction) or abs(correction) < 1e-9:
                continue
            if key.endswith(':S'):
                candidates = [f'S_GateForceX_{key[:-2]}']
            else:
                candidates = [f'GateForceX_{key}']
            for col in candidates:
                if col in data and data[col] is not None:
                    data[col] = data[col].astype(np.float64, copy=True) + correction
        return data

    def _load_peach_periodic_rows(self) -> Optional[List[Dict]]:
        """
        Load per-stroke periodic rows from the binary using rowtools.strokes.

        Returns a list of row dicts (sorted ascending by 'time') each containing:
          'time'            to stroke catch timestamp (ms, same clock as 50Hz Time)
          'rating'          to stroke rate (SPM)
          'boatspeed'       to boat speed (m/s)
          'dist_per_stroke' to distance per stroke (m)
          'power_N'         to per-seat power in Watts (N = 1 .. crew_size)
          For sculling boats also: 'left_power_N', 'right_power_N'

        Returns None on failure (e.g. unsupported boat layout, I/O error).
        """
        boat = {'8': '8+', '4': '4-', '2': '2-'}.get(str(self.boat_type), str(self.boat_type))
        try:
            ses = read_session(self.path, boat, with_samples=False)
        except (ValueError, RuntimeError) as exc:
            logger.warning('device stroke rows not available: %s', exc)
            return None
        rows: List[Dict] = []
        for i, t in enumerate(ses.time_ms):
            row = {'time': float(t), 'rating': float(ses.rate_spm[i]),
                   'boatspeed': float(ses.speed_mps[i]),
                   'dist_per_stroke': float(ses.distance_m[i])}
            for seat, sp in enumerate(ses.seats, start=1):
                row[f'power_{seat}'] = float(sp.total[i])
                if ses.sculling:
                    row[f'left_power_{seat}'] = float(sp.port[i])
                    row[f'right_power_{seat}'] = float(sp.starboard[i])
            rows.append(row)
        self._periodic_crew = ses.crew
        return rows or None

    def _load_xlsx_aperiodic(self) -> Optional[Dict]:
        """Try to load xlsx aperiodic data from a matching .xlsx file."""
        xlsx_path = self.path.with_suffix('.xlsx')
        if xlsx_path.exists():
            logger.info(f'Loading xlsx aperiodic: {xlsx_path}')
            return parse_xlsx_aperiodic(str(xlsx_path))
        return None

    def close(self) -> None:
        """Release large buffers held by this engine. Safe to call multiple times."""
        try:
            self.reader.close()
        except Exception:
            pass
        self._last_overview = None
        self._peach_rows = None
        self._xlsx_ap = None

    @property
    def start_utc(self) -> Optional[datetime]:
        return self.reader.start_utc

    @property
    def time_range_ms(self) -> tuple[int, int]:
        return self.reader.time_range_ms

    @property
    def duration_s(self) -> float:
        return self.reader.duration_ms / 1000.0

    def get_overview(self) -> OverviewData:
        """
        Generate lightweight session overview data.

        Reads the full file but only extracts per-second boat metrics
        (speed, power proxy, rate) for the overview chart. Fast because
        we only need 1 value per second from each 50-sample block.
        """
        data = self.reader.to_dict(skip_inactive=True, compute_derived=True,
                                   include_unknown=False)
        data = self._apply_force_offsets(data)
        n = len(data.get('Time', []))
        if n == 0:
            return OverviewData(
                time_s=np.array([]), boat_power=np.array([]),
                boat_speed=np.array([]), boat_rate=np.array([]),
                gps_lat=None, gps_lon=None, gps_time_ms=None,
                session_name=self.path.stem, start_utc=self.start_utc,
                boat_type=self.boat_type, crew_size=0,
                duration_s=0, total_strokes=0,
            )

        time_ms = data['Time']

        # Segment strokes: prefer Peach device timestamps (binary periodic rows)
        # which avoid GateAngle channel encoding issues. Fall back to GateAngle_1
        # local-minimum detection when no periodic rows are available.
        if self._peach_rows:
            strokes = segment_strokes_from_peach_timestamps(data, self._peach_rows)
            if not strokes:
                logger.debug('Peach-timestamp segmentation returned 0 strokes; falling back to GateAngle detection')
                strokes = segment_strokes(data)
        else:
            strokes = segment_strokes(data)

        # Per-stroke metrics
        stroke_times = np.array([s.time_ms for s in strokes]) / 1000.0
        stroke_rates = np.array([s.rate_spm for s in strokes])

        # Per-stroke average speed
        stroke_speeds = np.array([
            _safe_nanmean(s.channels['Speed_Boat'], default=0.0)
            if 'Speed_Boat' in s.channels and len(s.channels['Speed_Boat']) > 0 and _has_finite(s.channels['Speed_Boat'])
            else 0.0
            for s in strokes
        ])

        # Per-stroke average power per rower (Watts)
        # Prefer periodic layout crew (handles 8+ with 1 disconnected sensor correctly)
        crew_size = self._periodic_crew or self._detect_crew_size(data)
        stroke_power = np.zeros(len(strokes))

        # Prefer Peach device power (actual Watts) from periodic rows
        if self._peach_rows and not self.force_offsets_n:
            for s_idx, s in enumerate(strokes):
                # FIX: use the row that belongs to the stroke, not the nearest in time.
                pri = s.peach_row_index
                if pri is not None and 0 <= pri < len(self._peach_rows):
                    row = self._peach_rows[pri]
                    total = 0.0
                    count = 0
                    for seat in range(1, crew_size + 1):
                        pw = row.get(f'power_{seat}', 0) or 0
                        if np.isfinite(pw) and pw > 0:
                            total += pw
                            count += 1
                    stroke_power[s_idx] = total / max(count, 1)
        elif self.force_offsets_n:
            is_sculling = self._detect_sculling(data)
            eff_slip_kgf = self.sculling_slip_threshold_kgf if is_sculling else self.slip_threshold_kgf
            eff_wash_kgf = self.sculling_wash_threshold_kgf if is_sculling else self.wash_threshold_kgf
            corrected_periodic = compute_periodic_summary(
                strokes, crew_size,
                xlsx_ap=self._xlsx_ap,
                slip_threshold_kgf=eff_slip_kgf,
                wash_threshold_kgf=eff_wash_kgf,
            )
            for s_idx in range(len(strokes)):
                total = 0.0
                count = 0
                for seat in range(1, crew_size + 1):
                    arr = corrected_periodic.swivel_power.get(seat)
                    if arr is not None and s_idx < len(arr):
                        pw = float(arr[s_idx])
                        if np.isfinite(pw) and pw > 0:
                            total += pw
                            count += 1
                stroke_power[s_idx] = total / max(count, 1)
        else:
            # Fallback: average of mean |ForceX| across seats (proxy, not real W)
            for s_idx, s in enumerate(strokes):
                total = 0.0
                count = 0
                for seat in range(1, crew_size + 1):
                    for prefix in ('', 'S_'):
                        fx = f'{prefix}GateForceX_{seat}'
                        if fx in s.channels and _has_finite(s.channels[fx]):
                            total += _safe_nanmean(np.abs(s.channels[fx]), default=0.0)
                            count += 1
                stroke_power[s_idx] = total / max(count, 1)

        # Extract GPS track from binary
        gps_lat = None
        gps_lon = None
        gps_time_ms = None
        try:
            gps_points = extract_gps_from_blocks(self.reader.data)
            if gps_points:
                gps_lat = np.array([p['lat_deg'] for p in gps_points])
                gps_lon = np.array([p['lon_deg'] for p in gps_points])
                gps_time_ms = np.array([p['time_ms'] for p in gps_points])
        except Exception as exc:
            logger.warning(f'GPS extraction failed: {exc}')

        overview = OverviewData(
            time_s=stroke_times,
            boat_power=stroke_power,
            boat_speed=stroke_speeds,
            boat_rate=stroke_rates,
            gps_lat=gps_lat,
            gps_lon=gps_lon,
            gps_time_ms=gps_time_ms,
            session_name=self.path.stem,
            start_utc=self.start_utc,
            boat_type=self.boat_type,
            crew_size=crew_size,
            duration_s=self.duration_s,
            total_strokes=len(strokes),
        )
        return overview

    def analyze_piece(
        self,
        start_ms: float,
        end_ms: float,
        n_resample: int = N_RESAMPLE,
        force_offsets_n: Optional[Dict[str, float]] = None,
    ) -> PieceResult:
        """
        Full stroke analysis for a selected time segment.

        Reads only the needed 50Hz data, segments into strokes,
        computes per-rower average curves + SD, boat telemetry.
        """
        # Read segment with margin for boundary strokes
        margin_ms = 4000
        active_force_offsets = self.force_offsets_n if force_offsets_n is None else {
            str(k): float(v)
            for k, v in (force_offsets_n or {}).items()
            if v is not None
        }
        has_force_offsets = bool(active_force_offsets)

        data = self.reader.to_dict(
            start_ms=start_ms - margin_ms,
            end_ms=end_ms + margin_ms,
            skip_inactive=True,
            compute_derived=True,
            include_unknown=False,
        )
        data = self._apply_force_offsets(data, force_offsets_n=active_force_offsets)

        if len(data.get('Time', [])) == 0:
            raise ValueError(f"No active data in range {start_ms}-{end_ms}")

        # Segment strokes: prefer Peach device timestamps (binary periodic rows)
        # filtered to the requested time window.  Fall back to GateAngle_1 detection.
        # IMPORTANT: keep track of which filtered row list was used for segmentation
        # so that peach_row_index values (relative to that filtered list) are valid
        # when we later call compute_periodic_summary with the same list.
        strokes: List[StrokeInfo] = []
        _peach_rows_for_piece: Optional[List[Dict]] = None   # rows used for segmentation
        if self._peach_rows:
            # Filter periodic rows to the requested window (with margin)
            rows_in_range = [
                r for r in self._peach_rows
                if (start_ms - 4000) <= float(r['time']) <= (end_ms + 4000)
            ]
            if rows_in_range:
                strokes = segment_strokes_from_peach_timestamps(data, rows_in_range)
                if strokes:
                    _peach_rows_for_piece = rows_in_range
        if not strokes:
            logger.debug('Peach-timestamp segmentation returned 0 strokes; falling back to GateAngle detection')
            strokes = segment_strokes(data)
        if not strokes:
            raise ValueError(f"No active strokes found in range {start_ms}-{end_ms}")

        crew_size = self._periodic_crew or self._detect_crew_size(data)
        is_sculling = self._detect_sculling(data)

        # Compute per-rower curves (port side / sweep)
        rower_curves = {}
        for seat in range(1, crew_size + 1):
            curves = compute_rower_curves(strokes, seat, n_resample)
            if curves is not None:
                rower_curves[seat] = curves

        # Compute strokeside curves for sculling boats
        rower_curves_s = {}
        if is_sculling:
            for seat in range(1, crew_size + 1):
                curves_s = compute_rower_curves(
                    strokes, seat, n_resample, side_prefix='S_')
                if curves_s is not None:
                    rower_curves_s[seat] = curves_s

        # Compute boat curves
        boat = compute_boat_curves(strokes, n_resample)

        # Select sweep or sculling slip/wash thresholds based on detected boat type
        eff_slip_kgf = self.sculling_slip_threshold_kgf if is_sculling else self.slip_threshold_kgf
        eff_wash_kgf = self.sculling_wash_threshold_kgf if is_sculling else self.wash_threshold_kgf

        # Compute periodic summary.
        # Priority: xlsx (if present) > Peach binary periodic > 50Hz-derived.
        # If force offsets are configured, use the corrected 50Hz force channels
        # instead of Peach's original device power so power is recalculated.
        # xlsx provides all per-stroke metrics when available.
        periodic = compute_periodic_summary(
            strokes, crew_size,
            xlsx_ap=self._xlsx_ap,
            # Use the same filtered row list that was used for segmentation so that
            # stroke.peach_row_index (relative to that list) resolves correctly.
            peach_rows=None if has_force_offsets else _peach_rows_for_piece,
            slip_threshold_kgf=eff_slip_kgf,
            wash_threshold_kgf=eff_wash_kgf,
        )

        # Aggregate stats
        avg_rate = float(np.mean([s.rate_spm for s in strokes]))
        avg_speed = float(np.mean([
            _safe_nanmean(s.channels['Speed_Boat'], default=0.0)
            if 'Speed_Boat' in s.channels and len(s.channels['Speed_Boat']) > 0 and _has_finite(s.channels['Speed_Boat'])
            else 0.0
            for s in strokes
        ]))

        # Piece averages table, prefer periodic summary values (may include xlsx)
        averages = compute_piece_averages(
            strokes, crew_size, is_sculling, avg_rate, avg_speed,
            periodic=periodic,
            slip_threshold_kgf=eff_slip_kgf,
            wash_threshold_kgf=eff_wash_kgf,
        )

        return PieceResult(
            start_ms=start_ms,
            end_ms=end_ms,
            n_strokes=len(strokes),
            avg_rate=avg_rate,
            avg_speed=avg_speed,
            crew_size=crew_size,
            is_sculling=is_sculling,
            rower_names=self.rower_names,
            rower_curves=rower_curves,
            rower_curves_s=rower_curves_s,
            boat_curves=boat,
            periodic=periodic,
            averages=averages,
            strokes=strokes,
            start_utc=self.start_utc,
        )

    def _detect_crew_size(self, data: Dict[str, np.ndarray]) -> int:
        """Detect crew size from GateAngle columns that have real rowing data.

        Real oar angle data has std > 5° (oars sweep through ~80° per stroke).
        Empty/unused channels are flat noise near zero.
        """
        count = 0
        for seat in range(1, 9):
            key = f'GateAngle_{seat}'
            if key not in data:
                continue
            arr = data[key]
            if arr is None or len(arr) < 10:
                continue
            valid = arr[~np.isnan(arr)]
            if len(valid) < 10:
                continue
            # Real rowing data: significant variation (std > 5°) and range > 20°
            if np.std(valid) > 5.0 and (np.max(valid) - np.min(valid)) > 20.0:
                count = seat
        return max(count, 1)

    def _detect_sculling(self, data: Dict[str, np.ndarray]) -> bool:
        """Detect sculling boat from S_ (strokeside) channels or boat_type hint."""
        bt = self.boat_type.lower().strip()
        if bt in ('1x', '2x', '4x'):
            return True
        if bt in ('2-', '2', '4-', '4', '8', '8+'):
            return False
        # Auto-detect: sculling boats have S_GateAngle channels
        return any(k.startswith('S_GateAngle_') for k in data)



# ── Serialization Helpers ──────────────────────────────────────────────────────

def piece_to_save_dict(piece: PieceResult, session_name: str,
                       piece_name: str) -> Dict[str, Any]:
    """
    Convert a PieceResult into a dict suitable for saving as parquet/npz.

    Structure:
      metadata: JSON string with session info
      rower_{seat}_{channel}: float64 array [N_RESAMPLE]
      boat_{channel}: float64 array [N_RESAMPLE]
      periodic_{field}: float64 array [n_strokes]
    """
    save = {}

    # Metadata
    metadata = {
        'session_name': session_name,
        'piece_name': piece_name,
        'start_ms': piece.start_ms,
        'end_ms': piece.end_ms,
        'n_strokes': piece.n_strokes,
        'avg_rate': round(piece.avg_rate, 1),
        'avg_speed': round(piece.avg_speed, 3),
        'crew_size': piece.crew_size,
        'boat_type': getattr(piece, 'boat_type', None),
        'rower_names': piece.rower_names,
        'saved_at': datetime.now(timezone.utc).isoformat(),
    }
    save['_metadata'] = np.array([json.dumps(metadata)])

    # Per-rower average curves (mean + std)
    for seat, curves in piece.rower_curves.items():
        prefix = f'rower_{seat}'
        save[f'{prefix}_angle_mean'] = curves.angle_mean
        save[f'{prefix}_angle_std'] = curves.angle_std
        save[f'{prefix}_forcex_mean'] = curves.forcex_mean
        save[f'{prefix}_forcex_std'] = curves.forcex_std
        save[f'{prefix}_anglevel_mean'] = curves.anglevel_mean
        save[f'{prefix}_anglevel_std'] = curves.anglevel_std
        save[f'{prefix}_angle_raw_mean'] = curves.angle_raw_mean
        save[f'{prefix}_forcex_raw_mean'] = curves.forcex_raw_mean
        save[f'{prefix}_forcex_raw_std'] = curves.forcex_raw_std
        if curves.seatpos_mean is not None:
            save[f'{prefix}_seatpos_mean'] = curves.seatpos_mean
            save[f'{prefix}_seatpos_std'] = curves.seatpos_std
        if curves.seatposvel_mean is not None:
            save[f'{prefix}_seatposvel_mean'] = curves.seatposvel_mean
            save[f'{prefix}_seatposvel_std'] = curves.seatposvel_std
        if curves.seatpos_raw_mean is not None:
            save[f'{prefix}_seatpos_raw_mean'] = curves.seatpos_raw_mean
            save[f'{prefix}_seatpos_raw_std'] = curves.seatpos_raw_std
        if curves.seatposvel_raw_mean is not None:
            save[f'{prefix}_seatposvel_raw_mean'] = curves.seatposvel_raw_mean
            save[f'{prefix}_seatposvel_raw_std'] = curves.seatposvel_raw_std
        if curves.forcey_mean is not None:
            save[f'{prefix}_forcey_mean'] = curves.forcey_mean
            save[f'{prefix}_forcey_std'] = curves.forcey_std

    # Boat curves
    bc = piece.boat_curves
    for name in ['speed', 'accel', 'roll', 'pitch', 'yaw']:
        save[f'boat_{name}_mean'] = getattr(bc, f'{name}_mean')
        save[f'boat_{name}_std'] = getattr(bc, f'{name}_std')

    # Strokeside curves (sculling)
    for seat, curves in piece.rower_curves_s.items():
        prefix = f'rower_s_{seat}'
        save[f'{prefix}_angle_mean'] = curves.angle_mean
        save[f'{prefix}_angle_std'] = curves.angle_std
        save[f'{prefix}_forcex_mean'] = curves.forcex_mean
        save[f'{prefix}_forcex_std'] = curves.forcex_std
        save[f'{prefix}_anglevel_mean'] = curves.anglevel_mean
        save[f'{prefix}_anglevel_std'] = curves.anglevel_std
        save[f'{prefix}_angle_raw_mean'] = curves.angle_raw_mean
        save[f'{prefix}_forcex_raw_mean'] = curves.forcex_raw_mean
        save[f'{prefix}_forcex_raw_std'] = curves.forcex_raw_std
        if curves.forcey_mean is not None:
            save[f'{prefix}_forcey_mean'] = curves.forcey_mean
            save[f'{prefix}_forcey_std'] = curves.forcey_std

    # Piece averages (aperiodic-derived power, angles, peak force per rower)
    a = piece.averages
    avgs_dict = {
        'avg_rate': a.avg_rate,
        'avg_speed': a.avg_speed,
        'avg_distance_per_stroke': a.avg_distance_per_stroke,
        'duration_s': a.duration_s,
        'n_strokes': a.n_strokes,
        'is_sculling': piece.is_sculling,
    }
    for seat in range(1, piece.crew_size + 1):
        avgs_dict[f'swivel_power_{seat}']     = a.avg_swivel_power.get(seat)
        avgs_dict[f'peak_force_{seat}']       = a.peak_force.get(seat)
        avgs_dict[f'arc_{seat}']              = a.avg_arc_length.get(seat)
        avgs_dict[f'catch_angle_{seat}']      = a.avg_catch_angle.get(seat)
        avgs_dict[f'finish_angle_{seat}']     = a.avg_finish_angle.get(seat)
        avgs_dict[f'catch_slip_{seat}']       = a.avg_slip_angle.get(seat)
        avgs_dict[f'finish_slip_{seat}']      = a.avg_wash_angle.get(seat)
        avgs_dict[f'drive_start_ms_{seat}']   = a.avg_drive_start_ms.get(seat)
        avgs_dict[f'drive_time_ms_{seat}']    = a.avg_drive_time_ms.get(seat)
        avgs_dict[f'recovery_time_ms_{seat}'] = a.avg_recovery_time_ms.get(seat)
        avgs_dict[f'ratio_{seat}']            = a.avg_ratio.get(seat)
        avgs_dict[f'ang_max_f_{seat}']        = a.avg_angle_max_force.get(seat)
        avgs_dict[f'ang_70f_{seat}']          = a.avg_angle_70pct_force.get(seat)
        avgs_dict[f'work_j_{seat}']           = a.avg_work_j.get(seat)
        avgs_dict[f'effective_arc_{seat}']    = a.avg_effective_arc.get(seat)
        avgs_dict[f'work_q1_{seat}']          = a.avg_work_q1.get(seat)
        avgs_dict[f'work_q2_{seat}']          = a.avg_work_q2.get(seat)
        avgs_dict[f'work_q3_{seat}']          = a.avg_work_q3.get(seat)
        avgs_dict[f'work_q4_{seat}']          = a.avg_work_q4.get(seat)
        if piece.is_sculling:
            avgs_dict[f'swivel_power_p_{seat}']   = a.avg_swivel_power_port.get(seat)
            avgs_dict[f'swivel_power_s_{seat}']   = a.avg_swivel_power_stbd.get(seat)
            avgs_dict[f'peak_force_p_{seat}']     = a.peak_force_port.get(seat)
            avgs_dict[f'peak_force_s_{seat}']     = a.peak_force_stbd.get(seat)
            avgs_dict[f'arc_p_{seat}']            = a.avg_angle_range_port.get(seat)
            avgs_dict[f'arc_s_{seat}']            = a.avg_angle_range_stbd.get(seat)
            avgs_dict[f'catch_p_{seat}']          = a.avg_catch_angle_port.get(seat)
            avgs_dict[f'finish_p_{seat}']         = a.avg_finish_angle_port.get(seat)
            avgs_dict[f'catch_s_{seat}']          = a.avg_catch_angle_stbd.get(seat)
            avgs_dict[f'finish_s_{seat}']         = a.avg_finish_angle_stbd.get(seat)
    save['_averages'] = np.array([json.dumps(avgs_dict)])

    # Periodic summary
    p = piece.periodic
    if p is not None:
        save['periodic_time_ms'] = p.time_ms
        save['periodic_rate_spm'] = p.rate_spm
        save['periodic_duration_ms'] = p.duration_ms
        save['periodic_avg_speed'] = p.avg_speed
        for seat, peaks in p.peak_force.items():
            save[f'periodic_peak_force_{seat}'] = peaks
        # Aperiodic per-stroke arrays
        for seat, arr in p.catch_angle.items():
            save[f'periodic_catch_angle_{seat}'] = arr
        for seat, arr in p.finish_angle.items():
            save[f'periodic_finish_angle_{seat}'] = arr
        for seat, arr in p.drive_time_ms.items():
            save[f'periodic_drive_time_ms_{seat}'] = arr
        for seat, arr in p.recovery_time_ms.items():
            save[f'periodic_recovery_time_ms_{seat}'] = arr
        for seat, arr in p.work_j.items():
            save[f'periodic_work_j_{seat}'] = arr
        for seat, arr in p.swivel_power.items():
            save[f'periodic_swivel_power_{seat}'] = arr
        for seat, arr in p.slip_angle.items():
            save[f'periodic_slip_angle_{seat}'] = arr
        for seat, arr in p.wash_angle.items():
            save[f'periodic_wash_angle_{seat}'] = arr
        for seat, arr in p.arc_length.items():
            save[f'periodic_arc_length_{seat}'] = arr
        for seat, arr in p.effective_arc.items():
            save[f'periodic_effective_arc_{seat}'] = arr
        for seat, arr in p.drive_start_ms.items():
            save[f'periodic_drive_start_ms_{seat}'] = arr
        for seat, arr in p.ratio.items():
            save[f'periodic_ratio_{seat}'] = arr
        for seat, arr in p.angle_max_force.items():
            save[f'periodic_angle_max_force_{seat}'] = arr
        for seat, arr in p.angle_70pct_force.items():
            save[f'periodic_angle_70pct_force_{seat}'] = arr
        for seat, arr in p.work_q1.items():
            save[f'periodic_work_q1_{seat}'] = arr
        for seat, arr in p.work_q2.items():
            save[f'periodic_work_q2_{seat}'] = arr
        for seat, arr in p.work_q3.items():
            save[f'periodic_work_q3_{seat}'] = arr
        for seat, arr in p.work_q4.items():
            save[f'periodic_work_q4_{seat}'] = arr

    return save


def save_piece_npz(piece: PieceResult, session_name: str,
                   piece_name: str, output_path: str):
    """Save a PieceResult as a compressed numpy archive."""
    save = piece_to_save_dict(piece, session_name, piece_name)
    np.savez_compressed(output_path, **save)


def load_piece_npz(path: str) -> Dict[str, Any]:
    """Load a saved piece from numpy archive. Returns dict with metadata + arrays."""
    with np.load(path, allow_pickle=True) as npz:
        result = {}
        for key in npz.files:
            result[key] = npz[key]

    # Parse metadata
    if '_metadata' in result:
        result['metadata'] = json.loads(str(result['_metadata'][0]))
    return result


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    import time

    parser = argparse.ArgumentParser(description='Telemetry engine')
    sub = parser.add_subparsers(dest='command')

    # Overview
    ov = sub.add_parser('overview', help='Session overview stats')
    ov.add_argument('file', help='.peach-data file')
    ov.add_argument('--boat', default='8')

    # Analyze piece
    an = sub.add_parser('analyze', help='Analyze a piece')
    an.add_argument('file', help='.peach-data file')
    an.add_argument('--start', type=float, required=True)
    an.add_argument('--end', type=float, required=True)
    an.add_argument('--boat', default='8')
    an.add_argument('--save', type=str, default=None, help='Save as .npz')

    args = parser.parse_args()

    if args.command == 'overview':
        engine = TelemetryEngine(args.file, boat_type=args.boat)
        t0 = time.perf_counter()
        overview = engine.get_overview()
        dt = time.perf_counter() - t0
        print(f"Session: {overview.session_name}")
        print(f"  Duration: {overview.duration_s:.0f}s ({overview.duration_s/60:.1f} min)")
        print(f"  Crew: {overview.crew_size}, Boat: {overview.boat_type}")
        print(f"  Strokes: {overview.total_strokes}")
        print(f"  Computed in {dt:.2f}s")

    elif args.command == 'analyze':
        engine = TelemetryEngine(args.file, boat_type=args.boat)
        t0 = time.perf_counter()
        piece = engine.analyze_piece(args.start, args.end)
        dt = time.perf_counter() - t0
        print(f"Piece: {args.start/1000:.0f}s, {args.end/1000:.0f}s")
        print(f"  Strokes: {piece.n_strokes}")
        print(f"  Avg rate: {piece.avg_rate:.1f} spm")
        print(f"  Avg speed: {piece.avg_speed:.3f} m/s")
        print(f"  Crew: {piece.crew_size}")
        print(f"  Rower curves: {list(piece.rower_curves.keys())}")
        print(f"  Computed in {dt:.3f}s")

        if args.save:
            save_piece_npz(piece, 'test_session', 'test_piece', args.save)
            print(f"  Saved to {args.save}")


if __name__ == '__main__':
    main()
