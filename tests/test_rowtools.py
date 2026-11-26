import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fitparse
import numpy as np
import pytest

import synth
from rowtools import compare as C
from rowtools import excel as X
from rowtools import rita as F
from rowtools import strokes as S

PERIOD = 2500
T0 = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)


def ends(n_before=40, stop_ms=60_000, n_after=40):
    a = [10_000 + PERIOD * i for i in range(n_before)]
    b = [a[-1] + stop_ms + PERIOD * (i + 1) for i in range(n_after)]
    return a + b


def make(tmp_path, boat="1x", crew=1, power=None, raw=None, e=None):
    e = e or ends()
    scull = boat in ("1x", "2x", "4x")
    power = power if power is not None else [[200 + (i * 7) % 90 for i in range(len(e))] for _ in range(crew)]
    t, ch = synth.samples(e, crew, scull, power)
    rows = []
    for i, x in enumerate(e):
        r = []
        for s in range(crew):
            if raw and (s, i) in raw:
                r.append(raw[s, i])
            elif scull:
                p = power[s][i]
                r.append((1001 + p // 2, 1001 + p - p // 2, 1001 + p))
            else:
                r.append(1001 + power[s][i])
        rows.append((x, 24.0, 4.0, 10.0, r))
    return synth.peach_file(tmp_path / f"s_{boat}.peach-data", boat, rows, t, ch), e, power


@pytest.mark.parametrize("boat,crew", [("1x", 1), ("2x", 2), ("2-", 2), ("4-", 4), ("8+", 8)])
def test_power_per_boat(tmp_path, boat, crew):
    f, e, p = make(tmp_path, boat, crew)
    s = S.read_session(f, boat)
    assert s.crew == crew and list(s.time_ms) == e
    for k in range(crew):
        np.testing.assert_allclose(s.seats[k].total, p[k])


def test_raw_zero_is_missing_and_1001_is_zero_watts(tmp_path):
    f, e, _ = make(tmp_path, "4-", 4, raw={(0, 5): 0, (0, 6): 1001})
    s = S.read_session(f, "4-")
    assert np.isnan(s.seats[0].total[5])
    assert s.seats[0].total[6] == 0.0


def test_held_sculling_total_is_not_used(tmp_path):
    f, e, _ = make(tmp_path, raw={(0, 5): (0, 0, 1001 + 622)})
    assert np.isnan(S.read_session(f, "1x").seats[0].total[5])


def test_missing_power_rest_is_zero_work_is_left_out(tmp_path):
    e = ends()
    p = [[220] * len(e)]
    p[0][5] = 0                                       # no force in stroke 5
    f, _, _ = make(tmp_path, power=p, e=e, raw={(0, 5): (0, 0, 1001), (0, 9): (0, 0, 1001)})
    w, st = S.classify_power(S.read_session(f, "1x"), 1)
    assert st[5] == 1 and w[5] == 0.0
    assert st[9] == 2 and np.isnan(w[9])


def test_stroke_ends_at_its_row(tmp_path):
    """Work in [T_i-1, T_i] belongs to row i, not to row i-1."""
    rng = np.random.default_rng(3)
    e = [10_000 + PERIOD * i for i in range(80)]
    p = [[int(v) for v in rng.integers(100, 400, len(e))]]
    f, _, _ = make(tmp_path, power=p, e=e)
    s = S.read_session(f, "1x")
    w = S.seat_work_rate(s, 1)
    assert np.corrcoef(w[1:], s.seats[0].total[1:])[0, 1] > 0.99
    assert abs(np.corrcoef(w[1:-1], s.seats[0].total[2:])[0, 1]) < 0.4


def test_stop_is_not_part_of_a_stroke(tmp_path):
    f, e, _ = make(tmp_path)
    assert S.cycle_span_ms(S.read_session(f, "1x")).max() <= PERIOD + 1000


def test_untested_boat_refused(tmp_path):
    f, _, _ = make(tmp_path, "4x", 4)
    with pytest.raises(ValueError, match="untested-boats"):
        S.read_session(f, "4x")


def records(path):
    return [m.get_values() for m in fitparse.FitFile(str(path)).get_messages("record")]


def test_zero_and_gap_files(tmp_path):
    f, e, p = make(tmp_path)
    s = S.read_session(f, "1x")
    out = {}
    for mode in ("zero", "gap"):
        path = F.write_fit(F.build_records(s, 1, mode), T0, tmp_path / f"{mode}.fit", mode)
        out[mode] = records(path)
        ses = next(iter(fitparse.FitFile(str(path)).get_messages("session"))).get_values()
        assert ses["sport"] == "rowing"
        assert ("RecordingStrategy" in ses) == (mode == "gap")
    assert len(out["gap"]) == len(e)
    stops = [r for r in out["zero"] if r["power"] == 0]
    assert 50 <= len(stops) <= 60 and len(out["zero"]) == len(e) + len(stops)
    assert all(r["total_cycles"] == 40 for r in stops)
    ts = [r["timestamp"] for r in out["zero"]]
    assert all(b > a for a, b in zip(ts, ts[1:]))
    assert [r["power"] for r in out["gap"]] == p[0]
    assert out["gap"][0]["timestamp"].replace(tzinfo=timezone.utc) == T0 + timedelta(seconds=10)


def test_unreported_power_is_absent_from_the_fit(tmp_path):
    f, _, _ = make(tmp_path, raw={(0, 9): (0, 0, 1001 + 300)})
    s = S.read_session(f, "1x")
    r = records(F.write_fit(F.build_records(s, 1, "gap"), T0, tmp_path / "a.fit", "gap"))
    assert r[9].get("power") is None and r[8]["power"] and r[10]["power"]


def test_clock_is_local_time_at_session_zero(tmp_path):
    f, _, _ = make(tmp_path)
    s = S.read_session(f, "1x")
    assert F.resolve_start(s, None, 120) == s.header_time - timedelta(hours=2)
    with pytest.raises(ValueError):
        F.resolve_start(s, None, None)


def test_heart_rate_alignment(tmp_path):
    f, e, _ = make(tmp_path)
    s = S.read_session(f, "1x")
    recs = F.build_records(s, 1, "gap")
    hr = [F.Rec(x * 1000.0, None, None, None, None, None, 0, hr=100 + x % 60) for x in range(0, 60)]
    h = F.write_fit(hr, T0, tmp_path / "hr.fit", "zero")
    n = F.add_heart_rate(recs, T0, h)
    assert recs[0].hr == 110                           # stroke at 10 s
    assert n < len(recs) and recs[-1].hr is None       # never held past 5 s
    recs = F.build_records(s, 1, "gap")
    F.add_heart_rate(recs, T0, h, offset_s=4)
    assert recs[0].hr == 106                           # positive = heart rate later


def test_normalized_power():
    assert C.normalized_power(np.full(600, 250.0)) == pytest.approx(250)
    step = np.r_[np.full(1800, 100.0), np.full(1800, 300.0)]
    assert C.normalized_power(step) == pytest.approx(((100 ** 4 + 300 ** 4) / 2) ** 0.25, rel=0.01)
    assert C.tss(np.full(3600, 200.0), 200) == pytest.approx(100)


def test_known_work_and_method_ranking():
    """Intervals with known power and stops: only the reference and zeros keep the work."""
    e, w = [], []
    t = 0.0
    for _ in range(12):
        for _ in range(40):
            t += 2.4
            e.append(t)
            w.append(300.0)
        t += 90.0
    e, w = np.array(e), np.array(w)
    span = np.full(len(e), 2.4)
    true = float((w * span).sum())
    m = C.simulate(e, span, w)
    ref = list(m.values())[0]
    assert ref.sum() == pytest.approx(true, rel=0.01)
    np_ref = C.normalized_power(ref)
    assert C.normalized_power(m["stroke records + zeros in stops, joined"]) == pytest.approx(np_ref, rel=0.02)
    for k in ("stroke records with gaps, joined", "stroke records with gaps, held", "1 Hz sample and hold 15 s (known flawed)"):
        assert C.normalized_power(m[k]) > np_ref * 1.02


def test_match_columns_recovers_an_unknown_4x(tmp_path):
    e = [10_000 + PERIOD * i for i in range(60)]
    p = [[150 + 11 * s + (i * (5 + s)) % 70 for i in range(len(e))] for s in range(4)]
    t, ch = synth.samples(e, 4, True, p)
    f = synth.peach_file(tmp_path / "x.peach-data", "4x", [], t, ch)
    cols = [("Time", "")] + [(f"C{c}", "") for c in range(len(ch))]
    v = (ch & 0x7FFF).astype(float) - 8191
    perm = np.random.default_rng(5).permutation(len(ch))      # Excel order differs from file order
    rows = [[t[k]] + [v[c, k] / 16 for c in perm] for k in range(600, 4600)]
    x = synth.xlsx(tmp_path / "x.xlsx", [("Time", ""), ("SwivelPower", "1")], [[e[0], 100]], cols, rows)
    res = X.match_columns(f, x, skip=0)
    assert len(res) == 31
    for r in res:
        assert r["accepted"], r
        assert perm[int(r["column"].split("|")[0][1:])] == r["channel"]
        assert r["slope"] == pytest.approx(1 / 16, rel=1e-3)


def test_verify_and_match_strokes(tmp_path):
    e = [10_007 + PERIOD * i for i in range(200)]
    rng = np.random.default_rng(7)
    p = [[int(v) for v in rng.integers(120, 380, len(e))]]
    f, _, _ = make(tmp_path, power=p, e=e, raw={(0, 50): (0, 0, 1001 + p[0][49])})
    ex = [[t, p[0][i] if i != 50 else p[0][49]] for i, t in enumerate(e)]
    x = synth.xlsx(tmp_path / "v.xlsx", [("Time", ""), ("Rower Swivel Power", "1")], ex, [("Time", "")], [[0]])
    v = X.verify(S.read_session(f, "1x"), x)
    q = v["seats"][0]
    assert v["missed"] == 0 and q["off"] == 0
    assert q["not_reported"] == 1 and q["excel_repeats_there"] == 1
    m = X.match_strokes(f, x, 1)
    assert m["found"] == len(e) and m["seats"][0]["offset"] == 46 and m["seats"][0]["match"] > 0.95


def test_analysis_gives_each_stroke_its_own_power(tmp_path):
    telemetry_engine = pytest.importorskip("telemetry_engine")
    rng = np.random.default_rng(4)
    e = [10_000 + PERIOD * i for i in range(80)]
    p = [[int(v) for v in rng.integers(120, 380, len(e))]]
    f, _, _ = make(tmp_path, power=p, e=e)
    eng = telemetry_engine.TelemetryEngine(str(f), boat_type="1x")
    piece = eng.analyze_piece(*eng.time_range_ms)
    for st, w in zip(piece.strokes, piece.periodic.swivel_power[1]):
        i = int(np.searchsorted(e, st.time_ms))        # first row after the catch
        assert w == p[0][i]


def test_mmp_counts_rest_as_zero(tmp_path):
    from mmp import mmp

    e = ends(48, 120_000, 48)
    f, _, _ = make(tmp_path, power=[[300] * len(e)], e=e)
    m = mmp(S.read_session(f, "1x"), 1, (60, 300))
    assert m[60] == pytest.approx(300, rel=0.02) and m[300] < 260


def test_overlay_is_self_contained(tmp_path):
    from rowtools.viewer import write_overlay

    f, _, _ = make(tmp_path)
    s = S.read_session(f, "1x")
    fit = F.write_fit(F.build_records(s, 1), T0, tmp_path / "a.fit")
    html = write_overlay([fit], tmp_path / "o.html").read_text()
    assert not re.search(r"https?://|<script src|@import", html)


def test_no_flawed_1hz_export_in_the_code():
    root = Path(__file__).resolve().parents[1]
    for f in [*root.glob("src/**/*.py"), *root.glob("analysis/*.py")]:
        assert not re.search(r"resample_1hz|get_1hz|overlay_highres|hold_ms", f.read_text()), f


@pytest.mark.parametrize("sport,code", [("rowing", 15), ("kayaking", 41)])
def test_sport_is_written_to_session(tmp_path, sport, code):
    f, _, _ = make(tmp_path, "2x")
    s = S.read_session(f, "2x")
    fit = F.write_fit(F.build_records(s, 1), T0, tmp_path / "a.fit", sport=sport)
    ses = [m for m in fitparse.FitFile(str(fit)).get_messages("session")][0]
    assert ses.get_raw_value("sport") == code
