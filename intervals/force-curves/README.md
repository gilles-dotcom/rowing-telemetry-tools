# Peach FIT curves → Intervals.icu (prototype)

1. Create a computed **activity field** with code `PeachCurves`, type **Text**. Enable **Processes fit file messages**. Paste `01-import-curves.js` into its script.
2. Upload the CURVES FIT file. The ZERO file has no arrays.
3. Create/save the desired intervals on the activity power timeline.
4. Use **Actions → Re-process**, with **Keep intervals** enabled. This is file re-processing, not just Re-analyse.
5. Add a custom activity chart on the Data/Power page and paste `02-force-chart.js`.
6. Choose a saved piece from the chart dropdown. After changing boundaries, repeat step 4 and refresh the chart.

This prototype reads each activity's own FIT arrays. It stores compact averages, not the entire FIT file. Whole session is always included. Saved intervals are selected using elapsed stream time, with exclusive end boundaries. Curves are equally weighted and interpolated to 51 points across the recorded sample window, not the drive phase. Zeros count; missing samples do not. This version shows force vs normalized sample window only.

The current-intervals version stores timestamped stroke samples in `04-import-stroke-samples.js` and computes the piece averages in `05-current-intervals-chart.js`. The chart includes a `Refresh pieces` link that reloads the current activity; this re-runs the chart script against the latest saved intervals without re-processing the FIT file. The link is a page reload rather than a Plotly callback because Plotly chart buttons cannot execute an Intervals server-side script or fetch activity data.

The official JS data model exposes FIT fields through `.value`; these are SDK-scaled values. Unlike the Python fitparse inspection, the importer must not divide forces by ten again. Verify the first supplied file's handle curve peaks near 386.5 N, not 3865 N or 38.65 N, if inspecting an individual stroke in the script console.

Limitations: no verified hook to the live timeline drag selection; save the piece as an interval first. Not yet tested inside an Intervals account. Developer-field name exposure, computed Text field persistence, and whether saved intervals/streams are available during FIT processing must be verified there. The importer emits an error if curve arrays are unavailable. Do not publish to the shared chart library until tested.

References:
- https://github.com/intervals-icu/js-data-model (v1.2.0 declarations)
- https://forum.intervals.icu/t/computed-fields-from-fit-file-messages/46055
- https://forum.intervals.icu/t/custom-activity-charts/28627


Memory-limit update: the importer now iterates the FIT collection directly and reuses one interpolation buffer. Verified to produce identical output for 1,722 actual curves in a mocked SDK. This has not been tested against the server's memory quota. If memory errors persist, temporarily replace the field script with 00-diagnose-fit.js and re-process. It should report 2,249 records for the supplied file. If this also exceeds memory, even traversal without curve processing fails. Restore the importer after diagnosis; the chart cannot parse diagnostic text.

## Curves + table (06 + 07)
1. New activity field `PeachStrokes1`: Text, Processes fit file messages ON, hidden, Inline off. Paste `06-import-stroke-metrics.js`. Re-process.
   It reads only scalar stroke fields (drive/recovery, force, work, catch/finish/slip/wash/effective arc/peak-force angle per side), about 150 KB for this session.
   If memory runs out: SHARDS = 2 and a second field `PeachStrokes2` with SHARD = 2.
2. Replace the chart script with `07-curves-table-chart.js` (05 kept as a fallback). Set HEIGHT at the top to your sizing.
3. The dropdown updates both the curves and the table. Power, rate, split, HR and distance come from Intervals' own interval values; rowing metrics are averaged over all strokes inside the current interval.
4. Curves are still sampled by 04 (MAX_MESSAGES = 16 → about 10 strokes). Raise MAX_MESSAGES in 04 (128, then 512) to get real piece averages.

## Every stroke: PeachData (08) + probe (09) + chart 07
Problem: 04 samples 16 messages (short bouts get 0 curves) and 06 (PeachStrokes1) came back empty (table "-").
1. Probe: 4 temporary fields PeachProbe1..4 (Text, FIT ON) with 09-probe.js, TEST = touch / all / quarter / size. Re-process.
   Chart 07 lists what each probe stored below the curves. An empty probe means it failed.
2. Choose SHARDS: all OK and size = 256000 chars → 1. "all" missing but "quarter" OK → 4. "quarter" missing → 8+.
   Size cut off at X chars → SHARDS ≥ 215000 / X.
3. Fields PeachData1..SHARDS with 08-import-data.js (set SHARD per field, same SHARDS). Re-process. Delete the probes.
4. The chart prefers PeachData and falls back to PeachCurves/PeachStrokes. The diagnostics show strokes per shard;
   the table shows "Strokes found / expected" per interval (expected = Intervals' average rate × duration; ⚠ below 90%).
Local test: 4 shards = 418+424+423+428 = 1693 strokes (all strokes with StrokeDriveTime), about 52 KB per field.
