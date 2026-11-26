"""Check decoding against a PowerLine Excel export of the same session.

Naming trap: the export calls the per-stroke section "Aperiodic 0x800A" and the
50 Hz section "Periodic".

Exports contain names, GPS and serial numbers. Nothing here prints those, but
keep the files private.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

import numpy as np

from .samples import _channels_from_block_size, _decode_block, _find_aperiodic_blocks
from .strokes import StrokeSession, classify_power

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _shared(zf: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    out = []
    with zf.open("xl/sharedStrings.xml") as h:
        for _, e in ET.iterparse(h, events=("end",)):
            if e.tag == NS + "si":
                out.append("".join(t.text or "" for t in e.iter(NS + "t")))
                e.clear()
    return out


def _cells(row, shared: list[str]) -> list:
    vals: dict[int, object] = {}
    pos = 0
    for c in row.iter(NS + "c"):
        m = re.match(r"[A-Z]+", c.get("r") or "")
        if m:
            n = 0
            for ch in m.group():
                n = n * 26 + ord(ch) - 64
            pos = n
        else:
            pos += 1
        v, kind = c.find(NS + "v"), c.get("t")
        if kind == "inlineStr":
            vals[pos] = "".join(t.text or "" for t in c.iter(NS + "t"))
        elif v is not None and v.text is not None:
            vals[pos] = shared[int(v.text)] if kind == "s" else v.text
    return [vals.get(i) for i in range(1, max(vals) + 1)] if vals else []


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def read_section(xlsx: str | Path, name: str, code: str | None = None, skip: int = 0,
                 limit: int | None = None) -> tuple[list[str], np.ndarray]:
    """Column names ("Metric|seat") and values of one ===== section."""
    with ZipFile(xlsx) as zf:
        shared = _shared(zf)
        sheet = next(n for n in sorted(zf.namelist()) if n.startswith("xl/worksheets/sheet"))
        inside, hdr, seat, rows, n = False, None, None, [], 0
        with zf.open(sheet) as h:
            for _, e in ET.iterparse(h, events=("end",)):
                if e.tag != NS + "row":
                    continue
                if inside and seat is not None and n < skip:
                    n += 1
                    e.clear()
                    continue
                v = _cells(e, shared)
                e.clear()
                if v and v[0] == "=====":
                    if inside:
                        break
                    inside = len(v) > 1 and v[1] == name and (code is None or (len(v) > 2 and v[2] == code))
                elif inside and hdr is None:
                    hdr = v
                elif inside and seat is None:
                    seat = v
                elif inside:
                    n += 1
                    if v and math.isfinite(_num(v[0])):
                        rows.append([_num(x) for x in v[: len(hdr)]] + [math.nan] * (len(hdr) - len(v)))
                    if limit and len(rows) >= limit:
                        break
    if hdr is None:
        raise ValueError(f"section {name!r} not found")
    cols = [f"{str(a).strip()}|{'' if k >= len(seat or []) or seat[k] is None else str(seat[k]).split('.')[0]}"
            for k, a in enumerate(hdr)]
    return cols, np.array(rows, float).reshape(-1, len(hdr))


def excel_strokes(xlsx: str | Path, crew: int) -> tuple[np.ndarray, list[np.ndarray]]:
    cols, a = read_section(xlsx, "Aperiodic", "0x800A")
    seats = []
    for s in range(1, crew + 1):
        k = next((cols.index(c) for c in (f"Rower Swivel Power|{s}", f"SwivelPower|{s}") if c in cols), None)
        if k is not None:
            seats.append(a[:, k])
        else:
            sides = [a[:, cols.index(c)] for c in (f"P Swivel Power|{s}", f"S Swivel Power|{s}") if c in cols]
            seats.append(np.nansum(sides, axis=0) if sides else np.full(len(a), np.nan))
    return a[:, 0], seats


def verify(ses: StrokeSession, xlsx: str | Path, tol_w: float = 5.0) -> dict:
    t, seats = excel_strokes(xlsx, ses.crew)
    j = np.clip(np.searchsorted(ses.time_ms, t), 1, len(ses.time_ms) - 1)
    j = np.where(abs(ses.time_ms[j] - t) <= abs(ses.time_ms[j - 1] - t), j, j - 1)
    hit = abs(ses.time_ms[j] - t) <= 20
    res = {"excel_strokes": len(t), "decoded": len(ses.time_ms), "matched": int(hit.sum()),
           "missed": int((~hit).sum()), "seats": []}
    for s, ex in enumerate(seats, 1):
        got = ses.seats[s - 1].total[j]
        prev = np.r_[np.nan, ex[:-1]]
        both = hit & ~np.isnan(got) & ~np.isnan(ex)
        none = hit & np.isnan(got)
        _, status = classify_power(ses, s)
        res["seats"].append({
            "seat": s, "compared": int(both.sum()), "off": int((abs(got - ex)[both] > tol_w).sum()),
            "not_reported": int(none.sum()),
            "excel_repeats_there": int((abs(ex - prev)[none] < 0.05).sum()),
            "rest": int((status == 1).sum()), "left_out": int((status == 2).sum())})
    return res


def match_columns(peach: str | Path, xlsx: str | Path, rows: int = 4000, skip: int = 40_000) -> list[dict]:
    """Correlate every raw 50 Hz channel with every Excel column. Needs no
    knowledge of the boat: this is how the channel layouts were found."""
    cols, a = read_section(xlsx, "Periodic", skip=skip, limit=rows)
    if len(a) < 300:
        cols, a = read_section(xlsx, "Periodic", limit=rows)
    data = Path(peach).read_bytes()
    offs, size = _find_aperiodic_blocks(data)
    nch = _channels_from_block_size(size)
    ts, x = [], []
    for o in offs:
        t0, raw = _decode_block(data, o, nch)
        ts.append(t0 + np.arange(raw.shape[1]) * 20)
        x.append(raw)
    t, raw = np.concatenate(ts), np.concatenate(x, axis=1)
    order = np.argsort(t, kind="stable")
    t, raw = t[order], raw[:, order]
    j = np.clip(np.searchsorted(t, a[:, 0]), 0, len(t) - 1)
    ok = abs(t[j] - a[:, 0]) <= 1
    if ok.sum() < 300:
        raise ValueError("fewer than 300 samples line up in time: are these the same session?")
    out = []
    for c in range(nch):
        r = raw[c, j[ok]]
        v = (r & 0x7FFF).astype(float) - 8191
        cand = []
        for k in range(1, len(cols)):
            y = a[ok, k]
            m = (r != 0) & np.isfinite(y)
            if m.sum() < 300 or np.std(y[m]) < 1e-6 or np.std(v[m]) < 1e-6:
                continue
            slope, icpt = np.polyfit(v[m], y[m], 1)
            cand.append((abs(np.corrcoef(v[m], y[m])[0, 1]), cols[k], slope, icpt))
        cand.sort(reverse=True)
        best = cand[0] if cand else (0.0, "", math.nan, math.nan)
        out.append({"channel": c, "r": best[0], "column": best[1], "slope": best[2], "intercept": best[3],
                    "runner_up": cand[1][0] if len(cand) > 1 else 0.0,
                    "runner_up_column": cand[1][1] if len(cand) > 1 else "", "accepted": best[0] > 0.999})
    return out


def match_strokes(peach: str | Path, xlsx: str | Path, crew: int) -> dict:
    """Find the Excel stroke times in the file and look for power nearby.

    A start, not a recipe: see docs/untested-boats.md. Each u16 offset is tested
    as ``raw - 1001 == watts`` for strokes where Excel did not repeat a value.
    """
    t, seats = excel_strokes(xlsx, crew)
    b = np.frombuffer(Path(peach).read_bytes(), np.uint8)
    n = len(b) // 4
    u = b[: n * 4].view("<u4")
    keys = {int(round(v)): i for i, v in enumerate(t)}
    pos = np.flatnonzero(np.isin(u, np.array(list(keys), dtype=np.uint32))) * 4
    idx = np.array([keys[int(u[p // 4])] for p in pos])
    res = {"strokes": len(t), "found": len(set(idx.tolist())), "seats": []}
    for s, ex in enumerate(seats, 1):
        fresh = np.r_[False, abs(np.diff(ex)) > 0.05] & (ex > 5)
        use = fresh[idx]
        best = (0.0, None)
        for o in range(0, 400, 2):
            q = pos[use] + o
            q = q[q + 2 <= len(b)]
            raw = b[q].astype(int) | (b[q + 1].astype(int) << 8)
            y = ex[idx[use]][: len(q)]
            keep = raw >= 1001
            if keep.sum() < 100:
                continue
            score = float(np.mean(abs(raw[keep] - 1001 - y[keep]) <= 1.0))
            if score > best[0]:
                best = (score, o)
        res["seats"].append({"seat": s, "offset": best[1], "match": best[0]})
    return res
