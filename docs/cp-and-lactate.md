# Critical power and lactate models

**Status: some work, not a lot.** Both were built as pages in the first desktop version of our app (spring
2026) and used a little, then left behind when the work moved to the web app. The code was not kept, so
nothing here is runnable. What is left is the design: what to store, which models and methods, and the rules
we settled on. None of it was validated against lab tests or rowing-specific research.

## Critical power

**Efforts, not models.** We stored every effort as raw data (athlete, date, duration, power, work, and whether
it came from a session or was typed in by hand) and fitted models on top. A model can then be refitted when the
method changes or an effort turns out to be wrong. Efforts came from three places:

- maximal mean power from sessions, on the wall clock (`analysis/mmp.py`: rest counts as 0 W, so a 5 min value
  is a real 5 minutes, see [analysis](analysis.md));
- pieces a coach marked as "use for CP";
- manual entries, for example an ergometer test.

**Models.** Three were set up, each stored with its parameters, fit quality (R², adjusted R²) and the list of
efforts it used:

| Model | Parameters | Note |
|---|---|---|
| 2-parameter | CP, W' | P = CP + W' / t. The usual start, for efforts between about 2 and 15 min |
| 3-parameter | CP, W', Pmax | adds a maximal power, so short efforts fit better |
| omni | CP, W', Pmax and more | one curve from sprint to long efforts |

What we would keep from it:

- A CP model is only as good as its efforts. Session MMP mostly gives submaximal values; flag real maximal
  pieces or tests before you trust CP or W' (reasoned).
- Fit **per boat family** at least (sweep and sculling separately, see
  [sweep and sculling](sweep-and-sculling.md)), and keep on-water and ergometer efforts apart: Peach measures at
  the oarlock, an ergometer at the flywheel (reasoned).
- On-water power from a stroke file needs zeros in stops before you take MMP from it, or long windows read too
  high ([stroke or seconds](stroke-or-seconds.md)).

Not done: checking the fits against lab tests, choosing a model per athlete, and any tracking of CP over time.

## Lactate

A test is a date, an athlete, a protocol, the environment (ergometer, boat, cycling) and equipment, and a list
of steps: power, lactate (mmol/L), and optionally heart rate, RPE, the actual step duration and a note.

**Protocols**: step test (equal steps), LT1 steps (low-intensity steps for the aerobic threshold, steps may be
irregular), ramp, and freeform (field data, mixed intensities).

**Threshold methods**: Dmax, modified Dmax, fixed 4 mmol/L (OBLA) and log-log for LT1, on a spline through the
points. Rules we settled on:

- 2 points: draw a line, no thresholds. 3 points: try the spline, compute thresholds, warn when R² < 0.85.
  4 or more points that rise in order: full fit.
- Freeform tests: no Dmax and no 4 mmol. Their lactate often does not rise in order, and Dmax on such a curve
  means nothing. At most log-log LT1 with 4 or more good points.
- LT1 step tests: prefer log-log (LT1) and modified Dmax (LT2); skip 4 mmol.
- When two methods give the same threshold (within 5 W), show one, in this order: log-log LT1, modified Dmax,
  Dmax, 4 mmol. Otherwise four lines lie on top of each other.
- Heart rate and RPE at a threshold: interpolate from the steps at the threshold power.
- Comparing tests: LT1, LT2, W/kg when body weight is known, and the change since that athlete's previous test.

Not done: the full "lactate diary" (all protocols, per-step drill-down, multi-athlete compare) was designed but
only partly built, and the threshold methods were never compared with a lab's own results.

## If you pick this up

Keep raw efforts and raw steps, fit on top, and store how every number was made. Our design for both did that;
the models themselves are standard and available in other open tools.
