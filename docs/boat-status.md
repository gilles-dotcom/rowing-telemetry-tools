# Boat status

Checked against PowerLine Excel exports of the same sessions: decoded per-stroke power vs Excel per-stroke
power, matched on time within 20 ms. 3 sessions per boat type. Numbers are totals over all seats.

| Boat | Strokes in Excel | Not found by our scan | Seat values compared | Off by more than 5 W | Not reported by the device |
|---|---|---|---|---|---|
| 1x | 4,952 | 8 (0.2%) | 4,922 | 0 | 22 |
| 2x | 4,904 | 23 (0.5%) | 9,351 | 0 | 411 |
| 2- | 5,227 | 51 (1.0%) | 10,243 | 2 | 109 |
| 4- | 2,976 | 111 (3.7%) | 9,817 | 0 | 1,643 |
| 8+ | 4,472 | 68 (1.5%) | 29,917 | 10 | 5,315 |
| 4x | none available | | | | |

**Where the device reports a value, decoding matches Excel in 64,238 of 64,250 cases (measured).**

"Not reported" means the raw value is 0, which the file uses for "no value"; a measured 0 W is stored
differently (see [file format](findings/file-format.md)). Excel shows the previous stroke's value in those
places (7,362 of 7,500); in sculling the separate total field repeats it as well (431 of 431). Neither is a
measurement. The export uses the 50 Hz force data to decide:

- no work in the stroke (under 10% of the seat's typical work): exported as 0 W, the seat was resting;
- clear work: power is left out of that record, never 0 and never the previous value.

The 10% threshold is a choice, not a finding. `rowtools verify` prints these counts per seat.

Other checks:

- The stroke a device row belongs to: the row is written when the stroke **ends**. Correlating the 50 Hz
  work in [T(i-1), T(i)] with device power fits row i best in 50 of 51 seats (measured).
- The 50 Hz channels match Excel exactly after scaling, on all five boat types (measured, see
  [untested boats](untested-boats.md)).
- Every Excel stroke time exists in the file. Our scanner still misses up to 4.6% of strokes per session,
  most in the 4-. Those strokes are absent from the FIT. Open issue.

**4x: never tested.** There was no 4x session with an Excel export. The code has a 4x layout guess (144-byte
stroke rows, 6 channels per sculler); treat it as unverified.
