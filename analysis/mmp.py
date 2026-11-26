"""Maximal mean power on the wall clock: rest and gaps count as 0 W, never removed."""
from __future__ import annotations

import numpy as np

from rowtools.compare import spans_to_seconds
from rowtools.strokes import StrokeSession, classify_power, cycle_span_ms

DURATIONS_S = (5, 10, 15, 30, 45, 60, 90, 120, 180, 240, 300, 600, 720, 1200, 1800, 2700, 3600)


def mmp(ses: StrokeSession, seat: int, durations=DURATIONS_S) -> dict[int, float]:
    """Best mean power per duration for one seat, in watts (1 s resolution).

    Strokes the device did not report while the seat was working are filled by
    joining their neighbours, see rowtools.strokes.classify_power.
    """
    power, _ = classify_power(ses, seat)
    if np.isnan(power).all():
        return {}
    w = spans_to_seconds(ses.time_ms / 1000.0, cycle_span_ms(ses) / 1000.0, power)
    cum = np.r_[0.0, np.cumsum(w)]
    return {d: float(((cum[d:] - cum[:-d]) / d).max()) for d in durations if d <= len(w)}
