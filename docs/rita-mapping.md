# FIT and the Rowing Data Standard

The export follows the native field mappings of the Rowing Data Standard 1.0-RC, commit `a6225409`
(https://github.com/Rowing-Industry-Trade-Association/rowing-data-standard). It uses few developer fields on
purpose: most rowing metrics could not be checked well enough to publish.

| Peach value | FIT field | Status |
|---|---|---|
| stroke end time | `timestamp` | exact, rounded to 1 s (FIT) |
| seat power, device value | `power` | exact where reported; left out when the device did not report it while the seat worked |
| stroke rate | `cadence` (rounded), `cadence256` | exact; zero records carry only `cadence` 0 |
| boat speed, device per stroke | `enhanced_speed` | exact |
| distance from the first stroke, from 50 Hz speed | `distance` | measured, not checked against GPS |
| stroke count | `total_cycles` | exact; repeats on zero records |
| GPS | `position_lat`, `position_long` | nearest fix within 5 s, matched; `--no-gps` leaves it out |
| heart rate from another FIT | `heart_rate` | nearest sample within 5 s, never held longer |
| boat family | session `sport` | `rowing`; sculling as `kayaking` with `--sculling-sport kayaking`, see [sweep and sculling](sweep-and-sculling.md) |
| recording strategy | developer field 10 on the session | written only with `--stops gap` (StrokeBoundary) |
| distance per stroke | `cycle_length16` | not written: the FIT library used cannot encode it |
| drive and recovery time | fields 1 and 3 | not written: device rows come 60 to 280 ms after the finish, so times from them are biased |
| forces, angles, slip, wash, curves | fields 6, 7, 11 to 18, 60, 200 to 211 | not written: Peach measures force at the pin, the standard means the handle, and the force unit is not confirmed |

## Power cycle

Device power is a whole-cycle average: the recovery and the following drive, ending at the device row.
Open pull request #68 in the standard asks producers to use one cycle (catch to catch or finish to finish)
for every stroke metric. This export only writes power from the device, so it does not mix cycles.

## Stops

See [stroke or seconds](stroke-or-seconds.md). With `--stops zero`, records that are not strokes are
added, which the standard does not describe. Do not call that file conforming.

## Curves

The standard allows one in-stroke curve (handle force) in a stroke-by-stroke file. `studies/curves_prototype`
writes per-oar force and angle curves for a 1x, for the Intervals.icu charts. It is a prototype: it writes
pin force into the handle-force field and mixes cycles. Kept because it shows that curves can travel in a FIT
file and be drawn inside Intervals.icu.

Points we raised or would raise with the standard: force reference point (handle, pin, stretcher); rigging
fields (inboard, oar length) to convert pin to handle force; crew boats (seat, side, boat class, shared
session id); what native `power` means across systems; per-side power; a sample interval finer than whole ms.
