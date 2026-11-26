# Heart rate, Intervals.icu and the overlay

## Heart rate

`rowtools convert FILE --seat N --hr watch.fit --hr-offset S` puts the nearest heart-rate sample (within
5 s, never held longer) on each record. A positive offset moves heart rate later. Get the UTC offset of
the logger right first ([time](findings/time.md)); most "offsets" in practice were the clock.

`rowtools.hr_merge` is the older general tool: it merges heart rate from one FIT into another FIT (for
example an ergometer file), with automatic offset search and an optional power factor. It rebuilds records
from a fixed list of native fields, so developer fields in the base file are not kept.

## Intervals.icu

- `rowtools push FILE.fit` uploads with your own API key (Intervals.icu settings). Set
  `INTERVALS_API_KEY` and `INTERVALS_ATHLETE_ID`. Nothing else is sent.
- `rowtools.intervals.IntervalsClient` can also list activities, download an activity FIT, or rebuild a FIT
  from streams when the original is not available (activities that came from Strava do not expose
  their file).
- `intervals/splitwatt.md`: a custom stream for RP3 ergometer sessions that shows RP3 power x 1.08. Not for Peach files.
- `intervals/force-curves/`: computed fields and custom charts that read per-stroke force curves from a
  FIT file and draw them per interval inside Intervals.icu. Works with files from
  `studies/curves_prototype`. Read its README for the memory limits we hit and the sharding work-around.

## Overlay

`rowtools view a.fit b.fit --out overlay.html` writes one HTML page with no network access. Power, heart
rate, rate and pace, drag to zoom, a time offset per file. It shows the file's own records joined, and leaves
a break over 15 s open, so a stop looks like a stop.
