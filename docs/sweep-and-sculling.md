# Sweep and sculling: give them different activity types

**Export sculling and sweep sessions with different FIT sports.** Otherwise every platform treats them as one
sport and mixes them.

## Why it matters

Training platforms keep their numbers **per activity type**: the power-duration curve (best 1 min, 5 min,
20 min), FTP or eFTP estimates, power and heart-rate zones, and the training load built on them. A sculler's
power and a sweep rower's power come from different boats, movements and oar loads. When both arrive as
"Rowing":

- the best efforts of one overwrite the other in a single power curve;
- the FTP estimate, and with it every zone and every load number, is a mix of two sports;
- a progress chart shows boat changes, not fitness changes.

FIT itself cannot tell them apart: there is one sport `rowing` with the sub-sports `generic` and
`indoor_rowing`, and the Rowing Data Standard has no boat-class field yet (one of the points in
[rita-mapping](rita-mapping.md)). So the choice has to be made at export time.

## How

```bash
rowtools convert session.peach-data --sculling-sport kayaking
```

Sculling boats (1x, 2x, 4x) are then written as FIT sport `kayaking`; sweep boats stay `rowing`. Without
the option, sculling is written as `rowing` and `convert` prints a reminder.

`kayaking` is a workaround: it is the closest water sport that platforms already know and keep separate. Our
original app used this with Intervals.icu for clubs that row both. In Intervals.icu, set up the Kayaking
sport settings (FTP, zones) next to Rowing, and keep the zone method the same if you compare the two. We did
not check how TrainingPeaks, Strava or Garmin Connect display a kayaking file from a rowing boat.

Pick one convention and keep it. Changing it later splits one athlete's history in two places.

## The same rule inside your own analysis

Athlete development tracking (records, power-duration curves, technique metrics over months) should also be
kept per boat family. Our first version combined sweep and sculling into one line and was misleading: an
athlete who moved from the pair to the double looked as if they had changed. Filter by sweep or sculling
first, and default to the athlete's most recent boat family.

Technique thresholds differ too: `analysis/telemetry_engine.py` has separate slip and wash thresholds for
sweep and sculling, because peak forces differ.
