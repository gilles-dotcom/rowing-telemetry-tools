"""How a FIT file looks to a platform that works in seconds.

Stroke records have no value between strokes. A platform must invent one, and
the choice changes average and Normalized Power. These functions show by how
much. They write no FIT files.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

METHODS = ("linear", "hold", "hold5", "points")


def normalized_power(watts: np.ndarray) -> float:
    """30 s rolling mean, 4th-power mean, on a 1 s series."""
    if len(watts) < 30:
        return float("nan")
    r = np.convolve(watts, np.ones(30) / 30.0, "valid")
    return float(np.mean(r ** 4) ** 0.25)


def best(watts: np.ndarray, seconds: int) -> float:
    if len(watts) < seconds:
        return float("nan")
    return float(np.convolve(watts, np.ones(seconds) / seconds, "valid").max())


def tss(watts: np.ndarray, ftp: float) -> float:
    n = normalized_power(watts)
    return len(watts) * n * n / (ftp * ftp * 3600.0) * 100.0


def load_fit_power(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    import fitparse

    t, p = [], []
    for m in fitparse.FitFile(str(path)).get_messages("record"):
        v = m.get_values()
        if v.get("power") is not None:
            t.append(v["timestamp"].timestamp())
            p.append(float(v["power"]))
    if not t:
        raise ValueError(f"no power in {Path(path).name}")
    return np.array(t) - t[0], np.array(p)


def to_seconds(t: np.ndarray, p: np.ndarray, method: str) -> np.ndarray:
    """t in seconds. linear: join the points. hold: keep the last value.
    hold5: keep it at most 5 s. points: treat each record as one second."""
    if method == "points":
        return p.copy()
    grid = np.arange(0.0, t[-1] + 1.0)
    if method == "linear":
        return np.interp(grid, t, p)
    i = np.clip(np.searchsorted(t, grid, side="right") - 1, 0, len(p) - 1)
    out = p[i].copy()
    if method == "hold5":
        out[grid - t[i] > 5.0] = 0.0
    elif method != "hold":
        raise ValueError(f"unknown method {method!r}")
    return out


def summary(watts: np.ndarray, ftp: float | None = None) -> dict[str, float]:
    d = {"avg": float(watts.mean()), "np": normalized_power(watts),
         "best30s": best(watts, 30), "best5min": best(watts, 300)}
    if ftp:
        d["tss"] = tss(watts, ftp)
    return d


# --- one fixed stroke series, many ways to make seconds out of it ---------------

def spans_to_seconds(end_s: np.ndarray, span_s: np.ndarray, watts: np.ndarray, hz: int = 50) -> np.ndarray:
    """Each stroke's average power over the seconds the stroke lasted, 0 elsewhere.

    Energy-conserving, so this is the reference for the other methods.
    NaN power (not reported) is filled by joining its neighbours.
    """
    w = np.interp(np.arange(len(watts)), np.flatnonzero(~np.isnan(watts)), watts[~np.isnan(watts)])
    t0 = end_s[0] - span_s[0]
    n = int(np.ceil(end_s[-1] - t0))
    grid = t0 + np.arange(n * hz) / hz
    fine = np.zeros(n * hz)
    for e, s, v in zip(end_s, span_s, w):
        a, b = np.searchsorted(grid, [e - s, e])
        fine[a:b] = v
    return fine.reshape(n, hz).mean(1)


def simulate(end_s: np.ndarray, span_s: np.ndarray, watts: np.ndarray, stop_s: float = 10.0) -> dict[str, np.ndarray]:
    ok = ~np.isnan(watts)
    t0 = end_s[0] - span_s[0]
    n = int(np.ceil(end_s[-1] - t0))
    grid = t0 + np.arange(n)
    te, we = end_s[ok], watts[ok]
    zx, zy = [], []
    for i in range(len(end_s)):
        if i and end_s[i] - end_s[i - 1] > stop_s:
            k = end_s[i - 1] + 1.0
            while k < end_s[i] - span_s[i]:
                zx.append(k)
                zy.append(0.0)
                k += 1.0
        if ok[i]:
            zx.append(end_s[i])
            zy.append(watts[i])

    def hold(limit: float) -> np.ndarray:
        j = np.clip(np.searchsorted(te, grid, side="right") - 1, 0, len(te) - 1)
        out = we[j].copy()
        out[(grid - te[j] > limit) | (grid < te[0])] = 0.0
        return out

    return {
        "each stroke over its own seconds (reference)": spans_to_seconds(end_s, span_s, watts),
        "stroke records + zeros in stops, joined": np.interp(grid, zx, zy, left=0.0),
        "stroke records with gaps, joined": np.interp(grid, te, we, left=0.0),
        "stroke records with gaps, held": hold(1e9),
        "stroke records with gaps, held at most 5 s": hold(5.0),
        "1 Hz sample and hold 15 s (known flawed)": hold(15.0),
    }
