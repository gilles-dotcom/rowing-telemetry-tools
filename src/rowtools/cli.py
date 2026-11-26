"""Command line. Everything runs on your own computer; only ``push`` goes online."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path


def _session(a):
    from .strokes import read_session

    return read_session(a.file, a.boat, experimental=getattr(a, "experimental", False))


def cmd_inspect(a) -> int:
    from .samples import _channels_from_block_size, _find_aperiodic_blocks

    offs, size = _find_aperiodic_blocks(Path(a.file).read_bytes())
    print(f"50 Hz blocks: {len(offs)}  block size: {size}  channels: {_channels_from_block_size(size) if size else 0}")
    s = _session(a)
    print(f"boat: {s.boat}  seats: {s.crew}  strokes: {len(s.time_ms)}  "
          f"GPS points: {len(s.gps)}  logger clock at 0: {s.header_time:%Y-%m-%d %H:%M:%S} (local time, not UTC)")
    from .strokes import classify_power

    for i in range(1, s.crew + 1):
        _, st = classify_power(s, i)
        print(f"  seat {i}: reported {int((st == 0).sum())}, resting (0 W) {int((st == 1).sum())}, left out {int((st == 2).sum())}")
    return 0


def cmd_convert(a) -> int:
    from .rita import add_heart_rate, build_records, resolve_start, write_fit

    s = _session(a)
    start = datetime.fromisoformat(a.start_utc.replace("Z", "+00:00")) if a.start_utc else None
    offset = a.utc_offset
    if start is None and offset is None:
        local = s.header_time.replace(tzinfo=None).astimezone() if s.header_time else None
        if local is None:
            print("No clock in the file: use --start-utc", file=sys.stderr)
            return 2
        offset = int(local.utcoffset().total_seconds() // 60)
        print(f"note: assuming the logger clock was on this computer's time zone (UTC{offset / 60:+g} h). "
              "Use --utc-offset MINUTES if not; some loggers do not follow summer time.")
    t0 = resolve_start(s, start, offset)
    if a.hr and s.crew > 1 and not a.seat:
        print("--hr needs --seat for a crew boat", file=sys.stderr)
        return 2
    sport = a.sculling_sport if s.sculling else "rowing"
    if s.sculling and sport == "rowing":
        print("note: sculling written as Rowing, the same type as sweep, so platforms mix both in one power "
              "curve and one set of zones. Use --sculling-sport kayaking to keep them apart.")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for seat in ([a.seat] if a.seat else range(1, s.crew + 1)):
        recs = build_records(s, seat, a.stops)
        if not any(r.power for r in recs):
            print(f"seat {seat}: no power, skipped")
            continue
        note = ""
        if a.hr:
            note = f", heart rate on {add_heart_rate(recs, t0, a.hr, a.hr_offset)} of {len(recs)} records"
        f = write_fit(recs, t0, out / f"{Path(a.file).stem}_seat{seat}.fit", a.stops,
                      include_gps=not a.no_gps, sport=sport)
        print(f"{f}  ({sum(r.is_stroke for r in recs)} strokes, {sum(not r.is_stroke for r in recs)} stop records{note})")
    return 0


def cmd_compare(a) -> int:
    from .compare import METHODS, load_fit_power, summary, to_seconds

    for f in a.files:
        t, p = load_fit_power(f)
        print(f"{Path(f).name}: {len(p)} records, {t[-1]:.0f} s")
        for m in METHODS:
            d = summary(to_seconds(t, p, m), a.ftp)
            print(f"  {m:8s} " + "  ".join(f"{k} {v:6.1f}" for k, v in d.items()))
    return 0


def cmd_simulate(a) -> int:
    from .compare import simulate, summary
    from .strokes import classify_power, cycle_span_ms

    s = _session(a)
    p, _ = classify_power(s, a.seat)
    for k, w in simulate(s.time_ms / 1e3, cycle_span_ms(s) / 1e3, p).items():
        print(f"{k:46s} " + "  ".join(f"{n} {v:6.1f}" for n, v in summary(w, a.ftp).items()))
    return 0


def cmd_verify(a) -> int:
    from .excel import verify

    v = verify(_session(a), a.xlsx)
    print(f"Excel strokes {v['excel_strokes']}, matched {v['matched']}, missed {v['missed']}")
    for q in v["seats"]:
        print(f"  seat {q['seat']}: {q['compared']} compared, {q['off']} more than 5 W off; "
              f"{q['not_reported']} not reported (Excel repeats the previous value in {q['excel_repeats_there']}), "
              f"exported as rest {q['rest']}, left out {q['left_out']}")
    return 0


def cmd_columns(a) -> int:
    from .excel import match_columns

    for m in match_columns(a.file, a.xlsx):
        print(f"ch {m['channel']:2d}  {'OK ' if m['accepted'] else '?  '} r {m['r']:.4f}  {m['column']:24s} "
              f"slope {m['slope']:.5f} intercept {m['intercept']:+.3f}   next {m['runner_up']:.3f} {m['runner_up_column']}")
    return 0


def cmd_strokes(a) -> int:
    from .excel import match_strokes

    m = match_strokes(a.file, a.xlsx, a.crew)
    print(f"{m['found']} of {m['strokes']} Excel stroke times found in the file")
    for q in m["seats"]:
        print(f"  seat {q['seat']}: best offset {q['offset']} bytes after the time stamp, {100 * q['match']:.0f}% of values agree")
    return 0


def cmd_view(a) -> int:
    from .viewer import write_overlay

    print(write_overlay(a.files, a.out))
    return 0


def cmd_push(a) -> int:
    from .intervals import IntervalsClient

    key, ath = os.environ.get("INTERVALS_API_KEY"), os.environ.get("INTERVALS_ATHLETE_ID")
    if not key or not ath:
        print("Set INTERVALS_API_KEY and INTERVALS_ATHLETE_ID first.", file=sys.stderr)
        return 2
    c = IntervalsClient(ath, key)
    for f in a.files:
        r = c.upload_fit(f, name=a.name or Path(f).stem)
        print(json.dumps({k: r.get(k) for k in ("id", "activities") if k in r}) if isinstance(r, dict) else "uploaded")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rowtools", description=__doc__)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, text, file=True, boat=True):
        p = sub.add_parser(name, help=text)
        p.set_defaults(fn=fn)
        if file:
            p.add_argument("file", help=".peach-data file")
        if boat:
            p.add_argument("--boat", default="auto", help="1x 2x 2- 4- 8+ (default: detect)")
            p.add_argument("--experimental", action="store_true", help="allow untested boats (4x)")
        return p

    add("inspect", cmd_inspect, "what is in a file")
    p = add("convert", cmd_convert, "one FIT per seat, one record per stroke")
    p.add_argument("--out", default="fit")
    p.add_argument("--seat", type=int)
    p.add_argument("--stops", choices=("zero", "gap"), default="zero")
    p.add_argument("--utc-offset", type=int, help="minutes the logger clock is ahead of UTC")
    p.add_argument("--start-utc", help="UTC at session time 0, overrides the file")
    p.add_argument("--hr", help="FIT file with heart rate")
    p.add_argument("--hr-offset", type=float, default=0.0, help="seconds, positive moves heart rate later")
    p.add_argument("--no-gps", action="store_true")
    p.add_argument("--sculling-sport", choices=("rowing", "kayaking"), default="rowing",
                   help="FIT sport for sculling boats; kayaking keeps them apart from sweep on platforms")
    p = add("compare", cmd_compare, "avg and NP of FIT files under different assumptions", file=False, boat=False)
    p.add_argument("files", nargs="+")
    p.add_argument("--ftp", type=float)
    p = add("simulate", cmd_simulate, "one session, many ways to make seconds (writes nothing)")
    p.add_argument("--seat", type=int, default=1)
    p.add_argument("--ftp", type=float)
    p = add("verify", cmd_verify, "check decoding against a PowerLine Excel export")
    p.add_argument("xlsx")
    p = add("match-columns", cmd_columns, "find which channel is which Excel column", boat=False)
    p.add_argument("xlsx")
    p = add("match-strokes", cmd_strokes, "find where per-stroke power sits", boat=False)
    p.add_argument("xlsx")
    p.add_argument("--crew", type=int, required=True)
    p = add("view", cmd_view, "overlay FIT files in one local HTML page", file=False, boat=False)
    p.add_argument("files", nargs="+")
    p.add_argument("--out", default="overlay.html")
    p = add("push", cmd_push, "upload FIT files to Intervals.icu with your own key", file=False, boat=False)
    p.add_argument("files", nargs="+")
    p.add_argument("--name")

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.ERROR)
    try:
        return a.fn(a)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
