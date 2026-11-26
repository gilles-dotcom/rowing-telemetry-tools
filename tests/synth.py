"""Synthetic files only. No real session data in this repository."""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import numpy as np

CHANNELS_PER_SEAT = {"scull": 6, "sweep": 3}
STRIDE = {"1x": 48, "2x": 80, "4x": 144, "2-": 56, "4-": 80, "8+": 136}


def samples(end_ms, seats, sculling, watts, pad_s=5, period_ms=2500):
    """50 Hz raw channels: 7 boat + per seat angle/forceX/forceY."""
    n = int((max(end_ms) / 1000 + pad_s) * 50)
    n += -n % 50
    t = np.arange(n) * 20.0
    per = CHANNELS_PER_SEAT["scull" if sculling else "sweep"]
    ch = np.full((7 + seats * per, n), 8191, dtype=np.uint16)
    rng = np.random.default_rng(1)
    for c in range(7):
        ch[c] = 8191 + (200 * np.sin(t / (300 + 70 * c))).astype(int) + rng.integers(0, 4, n)
    for s in range(seats):
        ang, frc = np.zeros(n), np.zeros(n)
        for i, e in enumerate(end_ms):
            a, b = np.searchsorted(t, [e - period_ms, e])
            m = b - a
            if m < 20:
                continue
            x = np.arange(m) / m                       # recovery first, then drive
            rec = x < 0.55
            ang[a:b] = np.where(rec, 30 - 80 * x / 0.55, -50 + 80 * (x - 0.55) / 0.45)
            frc[a:b] = np.where(rec, 0, np.sin(np.pi * np.clip((x - 0.55) / 0.45, 0, 1))) * watts[s][i] * 2
        order = ["a", "a", "fx", "fx", "fy", "fy"] if sculling else ["a", "fx", "fy"]
        for k, kind in enumerate(order):
            jitter = 1 + 0.04 * k + 0.02 * s
            base = ang * 16 * jitter if kind == "a" else frc * jitter * (2 if kind == "fx" else 0.5)
            noise = rng.integers(-6, 7, n)
            ch[7 + s * per + k] = np.clip(8191 + base + noise, 1, 32000).astype(np.uint16)
    return t, ch


def row_bytes(boat, t, rate, speed, dist, raw):
    """raw: per seat (port, stbd, total) for sculling or one value for sweep."""
    size = STRIDE[boat]
    b = bytearray(size)
    struct.pack_into("<I", b, 0, int(t))
    struct.pack_into("<H", b, 8, 1)
    b[10] = int(round((rate + 0.5) * 2))
    struct.pack_into("<H", b, 12, int(round(speed * 256)) + 1)
    struct.pack_into("<H", b, 16, int(round(dist * 256)) + 1)
    for s, v in enumerate(raw):
        if boat in ("1x", "2x", "4x"):
            for rel, x in zip((22, 24, 46), v):
                if rel + s * 26 + 2 <= size:
                    struct.pack_into("<H", b, rel + s * 26, x)
        elif boat == "2-":
            struct.pack_into("<H", b, 34 + 2 * s, v)
            if s == 0:
                struct.pack_into("<H", b, 22, v)
        else:
            struct.pack_into("<H", b, 22 + 14 * s, v)
    return bytes(b)


def peach_file(path, boat, rows, t, ch, clock=1_780_000_000):
    head = bytearray(64)
    struct.pack_into("<I", head, 24, clock)
    blob = bytes(head) + b"".join(row_bytes(boat, *r) for r in rows) + bytes(16)
    blob += bytes(-len(blob) % 4)
    nch, size = len(ch), 28 + len(ch) * 100 + 8
    out = [blob]
    for k in range(0, ch.shape[1], 50):
        out.append(struct.pack("<7I", size, 0, 0, 0, int(t[k]), 1000, 1) + ch[:, k:k + 50].astype("<u2").tobytes() + bytes(8))
    Path(path).write_bytes(b"".join(out) + bytes(64))
    return Path(path)


def xlsx(path, stroke_cols, stroke_rows, per_cols, per_rows):
    def col(i):
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s

    lines = []

    def add(vals):
        r = len(lines) + 1
        cells = "".join(
            f'<c r="{col(i)}{r}"><v>{v!r}</v></c>' if isinstance(v, (int, float)) else
            f'<c r="{col(i)}{r}" t="inlineStr"><is><t>{v}</t></is></c>' for i, v in enumerate(vals) if v not in (None, ""))
        lines.append(f'<row r="{r}">{cells}</row>')

    add(["=====", "File Info"])
    add(["=====", "Aperiodic", "0x800A"])
    add([c[0] for c in stroke_cols])
    add([c[1] for c in stroke_cols])
    for x in stroke_rows:
        add([float(v) for v in x])
    add(["=====", "Periodic"])
    add([c[0] for c in per_cols])
    add([c[1] for c in per_cols])
    for x in per_rows:
        add([float(v) for v in x])
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData>{"".join(lines)}</sheetData></worksheet>')
    return Path(path)
