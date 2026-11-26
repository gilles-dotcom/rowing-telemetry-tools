# Stroke or seconds?

A Peach session is a list of **strokes**: each has an end time, a rate, a speed and a power. Training
platforms (Intervals.icu, TrainingPeaks, Strava, Garmin) think in **seconds**. Between two strokes, and
during every stop, the platform has no value and must invent one. How it does that changes average power,
Normalized Power (NP) and every training-load number built on NP.

## Five ways to make seconds out of strokes

| Method | During a stroke | While stopped | Energy |
|---|---|---|---|
| Each stroke's power over the seconds it lasted, 0 elsewhere | correct | 0 | kept (our reference) |
| Join the points (linear) | smooth | slides from the last stroke to the next one, over the whole stop | too high |
| Hold the last value | steps | the last stroke continues for the whole stop | too high |
| Sample once a second and hold up to 15 s (what our old app did) | 2 or 3 samples per stroke | the last stroke continues up to 15 s | too high, and per-stroke energy is not kept |
| Mean of instantaneous power per second | chaotic: huge in the drive, 0 in the recovery | 0 | kept, but useless to read |

## What it does to NP (measured)

15 sessions, 51 seats, five boat types. The same device stroke powers each time; only the method changes.
Percent difference from the reference, minimum / median / maximum over the seats:

| Method | 1x (3 seats) | 2x (6) | 2- (6) | 4- (12) | 8+ (24) |
|---|---|---|---|---|---|
| Stroke records + 0 W records in stops, joined | 0 / 0 / 0 | 0 / 0 / 0 | -1 / 0 / 0 | -2 / 0 / 0 | -1 / 0 / 0 |
| Stroke records, gaps in stops, joined | +1 / +6 / +7 | 0 / +1 / +7 | +2 / +3 / +22 | +7 / +14 / +66 | +1 / +8 / +14 |
| Stroke records, gaps, held | +1 / +7 / +7 | +1 / +2 / +18 | +3 / +6 / +23 | +6 / +14 / +51 | +1 / +11 / +17 |
| Stroke records, gaps, held at most 5 s | 0 / +1 / +2 | 0 / 0 / +1 | -2 / 0 / +1 | -3 / -1 / +1 | -6 / 0 / 0 |
| Sample and hold 15 s | +1 / +6 / +6 | 0 / +1 / +5 | +1 / +3 / +5 | 0 / +5 / +7 | 0 / +2 / +4 |

The actual FIT files written by `rowtools convert` (zeros in stops), read back on a 1 s grid:
joined -1.8% to +0.8%; held -1.3% to +1.8%, except one 8+ seat at +9.8%.

Reading it:

- The size of the error depends on how much of the session is stopped. Steady sessions barely move; a
  4- training with a third of the time stopped goes up to +66%.
- Training load (TSS and similar) scales with about the square of NP, so its error is about double.
- A stroke-by-stroke file **without** zeros is not better than a 1 Hz file. The zeros make the difference.
- None of the four platforms above has been measured here. Upload both versions and compare before you
  trust any number from them.

## What the export does

`rowtools convert` writes one record per stroke, at the time the stroke **ends**, plus one 0 W record per
second during stops longer than 10 s (`--stops zero`, the default). `--stops gap` writes only strokes and
marks the file `RecordingStrategy = StrokeBoundary`. The Rowing Data Standard describes one record per
stroke and does not describe records that are not strokes, so the zero file does not claim a strategy. The
question is open in the standard (issue #67).

## Assumptions behind the reference

A stroke lasts from the previous stroke's end, capped at 60 / rate + 1 s, so a stop never becomes part of a
stroke. NP is the 30 s rolling mean, 4th-power mean. Strokes the device did not report while the seat was
working are filled by joining their neighbours. The reference is a construction, not an independent truth.

Try it on your own file: `rowtools simulate session.peach-data --seat 1`.
