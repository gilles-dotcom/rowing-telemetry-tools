# PeachCurves v3: all inside Intervals.icu

Input: the exported `your curves FIT file`, unchanged.

## Why v3
The full importer hit "Memory limit exceeded". Every computed field runs as its own script, so v3
splits the import across several fields (shards). Each shard reads every K-th FIT message, and only the
port and starboard arrays. It stores 21-point curves as compact text, about 90 characters per stroke.
The chart combines all shards and averages over the activity's **current** saved intervals.
Handle = mean of port and starboard, which is how the converter defines HandleForceCurve.

## Setup
1. Settings → Custom fields → Activity field, **Text**, **Processes fit file messages ON**, hidden from the summary, Inline off.
   Code `PeachCurves1`. Paste `11-import-shard.js` and keep `SHARD = 1`, `SHARDS = 4`.
2. Repeat for `PeachCurves2`, `PeachCurves3` and `PeachCurves4`, changing only `SHARD`.
3. Re-process the activity (Actions → Re-process, keep intervals), or upload the FIT again.
4. Custom activity chart: paste `12-curves-chart.js`.

## If a shard still hits the memory limit
Raise `SHARDS` to 6 or 8 in **every** shard field, add the extra fields, and re-process.
The chart's status line shows `x/K shards`. The chart works with a subset of shards and
just averages fewer strokes. A single shard already covers the whole session evenly.
Local size: 4 shards = about 430 strokes and 39 KB per field; 1 shard = 155 KB.

## Using it
- Dropdown: whole session plus every saved interval, read fresh when the chart script runs.
- Slider: 60 s windows every 30 s across the session (`WINDOW_S`/`STEP_S` at the top of the chart).
- Status line: strokes · shards · saved intervals · **computed HH:MM:SS UTC**.
  If that time changes after you reload the page, the chart re-ran and picked up edited intervals.

## Verified locally (mock of icu.fit built from the FIT via fitparse)
- 4 shards → 429 + 428 + 431 + 434 = 1722 strokes, each counted exactly once.
- Piece averages match an independent Python calculation within 0.15 N (storage step 0.25 N).
- Chart JSON passes Plotly schema validation and renders (headless Chrome screenshot).

## Not yet verified in Intervals
- Per-field memory budget (is 430 strokes per shard under the limit?).
- Text field size limit (39 KB per field).
- Whether the chart re-runs on page reload after interval edits (use the computed timestamp).
- Plotly slider support inside Intervals.
- Time alignment: interval `start_time` vs FIT timestamp minus `start_date`.
