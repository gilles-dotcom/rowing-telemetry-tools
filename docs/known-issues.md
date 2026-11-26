# Known issues and open questions

- **Missed strokes**: up to 4.6% per session (mostly 4-). Every Excel stroke time is in the file, so a
  better scan could find them.
- **4x** untested, layout guessed.
- **Force unit** unconfirmed (raw / 16 in Excel; the reader uses raw x 0.5 as newtons).
- **50 Hz channels 2 and 3** unknown.
- **The 10% "resting" threshold** for strokes the device did not report is a choice.
- **Seat work vs device power** correlates 0.98 to 1.00 for most seats, but about 0.82 in one 2x session.
  Not investigated.
- **Platforms not measured**: what Intervals.icu, TrainingPeaks, Strava and Garmin do with stroke records and
  zero records is not checked here.
- **Zero records** are outside the Rowing Data Standard as written (issue #67 open).
- **The curves prototype** is 1x only, writes pin force as handle force, mixes cycles, and needs
  fit-tool 0.9.14 with the standard's library.
- **`hr_merge.py`** drops developer fields from the base file.
- **`viewer/reference`** is TypeScript from a web app with its imports; it does not run here.
- **The firmware bridge** is only an encoder for ANT+ power page 0x10, without the radio code. ANT+ has its
  own terms for using its profiles.
- **Sculling as `kayaking`** is a workaround; only Intervals.icu was used this way. Other platforms not checked.

## Questions only Peach can answer

If you get the chance to ask, these would close most of the gaps above:

- The force unit and any per-channel scaling.
- What channels 2 and 3 of the 50 Hz data are.
- How PowerLine decides that a stroke counts, and the exact stroke row layout per boat (would fix the missed
  strokes).
- Where the logger clock comes from (set by PowerLine, GPS or by hand), how much it drifts, and which time
  zone it is set to. This is what video sync and heart-rate merges depend on.
- A firmware or format version in the file, and notice before the format changes (one firmware update
  already changed the block layout, see [file format](findings/file-format.md)).
- The formulas behind PowerLine's per-stroke metrics (catch and finish angle, slip, wash, effective length).
- Whether the 50 Hz distance is meant to count from the start of recording.
- The seat position scale, and the full list of sensors and the channel order they produce.
- **Strokes outside rowing.** `TelemetryEngine.analyze_piece` finds false "strokes" in time without rowing (rigging,
  warm-up on the landing stage): high rates and 0 W. Analyse windows where the crew is rowing.
