# Analysis

`analysis/telemetry_engine.py` is the analysis code from the original app, with fixes listed below.

- **Stroke segmentation**: catch = oar angle minimum inside each device stroke interval. Sweep and sculling,
  missing seats, and boats where the front seats have no sensor (the first rowing angle signal is used).
- **Curves**: force, angle, angular velocity, seat and stretcher channels per stroke, resampled to 200
  points, mean and spread per piece; force against angle; boat speed and acceleration.
- **Technique metrics** per stroke and piece: catch and finish angle, slip and wash (force thresholds),
  effective arc, peak force angle, work, drive time. Port and starboard separately for sculling.
- **Device power** per stroke from the device rows, which now belong to the right stroke.
- `analysis/mmp.py`: maximal mean power on the wall clock. Rest counts as 0 W and is never removed, so a
  5 min value is a real 5 minutes.

```python
from telemetry_engine import TelemetryEngine
e = TelemetryEngine("session.peach-data", boat_type="2x")
piece = e.analyze_piece(*e.time_range_ms)
```

Fixes made for this release (1 to 4 are tested, see `tests/`):

1. Each stroke took the **previous** stroke's device power, because the device row is written at the end
   of the stroke. In steady rowing this barely shows; at a stop it moved a hard stroke onto a paddle stroke.
2. The last stroke before a stop ran on to the first catch after it (up to 16 s). Windows are now capped at
   the device period plus 1 s.
3. A missing device value became 0 W. It now stays missing.
4. Stroke rows come from `rowtools.strokes`, which reads "no value" correctly.
5. With a matching `.xlsx` next to the file, Excel's angle and timing metrics replace the computed ones,
   but power no longer does: Excel repeats the previous stroke where the device had no value.

Not fixed and worth knowing: forces are in an unconfirmed unit (see [file format](findings/file-format.md)),
so slip and wash thresholds are relative. Force-based power from 50 Hz (force x angular velocity x 0.88 m)
came out about a third above device power in our files; use device power.

## Force offset after a pause (measured on our sessions)

Peach zeroes the force sensors automatically. If there is still pressure on the blade at that moment, for
example during a pause with the blade held in the water, the zero is wrong for the rest of the session. You
see it in force against angle: the recovery line, normally at about -10 to -25 N for every rower, spreads out
(we saw +14 to -63 N within one crew). A wrong zero changes drive force, so work and power from 50 Hz are
wrong too, not only the chart.

How we handled it:

- **Warn** when the recovery baselines of a crew spread more than about 45 N, or one rower is more than about
  25 N from the median.
- **Correct** only a rower who is at least 30 N from the stroke median, by adding a constant to that rower's
  force X. Smaller differences are normal rower-to-rower variation; pulling them to the median hides real
  technique.
- Recompute everything that depends on force after the correction.

In code: `TelemetryEngine(path, force_offsets_n={"3": 22.0, "1:S": -15.0})` adds the offset in newtons to seat
3's force (sculling: `"1:S"` is seat 1 starboard), before any analysis. With offsets set, the engine uses power
from the corrected 50 Hz force instead of the device power, which came out about a third high in our files
(see above), so read corrected power as relative. The thresholds are our choice, not a Peach rule. The device
power itself is computed on the logger and is not changed by an offset.

## Upside-down boat sensors

After a bad calibration some boat sensors report motion with the wrong sign. Our display rule: if the positive
peak of boat acceleration is larger than the negative one, negate acceleration for display, so the big check at
the catch always points down. Flip roll, pitch and yaw **only** when acceleration was flipped for that same piece.
Never flip speed, pace or the rowers' channels, and never write the flipped sign back into the data.
`viewer/reference/CurveChart.tsx` shows the display side.

## When a curve viewer should switch strokes

For a coach the drive matters most. Keep the current stroke's curve on screen until its finish (maximum
angle), and switch to the next stroke during the recovery. Stored curves still run catch to catch. A live
marker may move smoothly through the catch, but it must never pick which stroke is shown. The same rule made
video review work, see [video sync](video-sync.md).

## Pieces

Pieces (the parts of a session you analyse) were marked by the coach in our app. The only automatic version
was a simple rule in an early demo: a new piece starts after more than 5 s without a stroke. That splits a
session at every stop, including short ones, so treat it as a starting point for a coach to edit, not as
piece detection.
