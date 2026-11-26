# `.peach-data` file format (as far as we know)

Found by comparing raw bytes of our own files with PowerLine's Excel exports of the same sessions.
Labels: **matched** (equal to Excel), **measured**, **reasoned**, **guess**.

## Layout

- Little-endian throughout (matched).
- Header: `u32` at byte 24 = logger clock at session time 0, local time (measured, see [time](time.md)).
- The file contains linked chains of blocks. PowerLine follows the chains, not the file order (measured: a
  hand-made file only opened after the chain pointers were correct).
- **50 Hz sample blocks.** Header of 7 `u32`: block size, 3 unknown, block start time (ms), 1000, 1. Then
  `channels x 50` `u16` values, channel by channel, in most files followed by 8 bytes of padding.
  Channels = (size - 28) / 100, or (size - 36) / 100 with padding (matched). Older firmware wrote one sample per block (about 82 bytes); newer files have 50 per block.
- **GPS blocks**, 352 bytes, 10 one-second fixes each, offsets in metres from a reference position stored
  elsewhere in the file (matched against Excel GPS). See `extract_gps_from_blocks`.
- **Stroke rows**: one per stroke, a `u32` time stamp followed by fixed fields (below). They are found by
  scanning for runs of time stamps 100 ms to 10 s apart with plausible values. Some newer files hold extra
  rows between the 50 Hz blocks ("side-band"); scanning there recovered most strokes the first scan missed.

## 50 Hz channels

Value = `(raw & 0x7FFF) - 8191`, raw 0 = no data, bit 15 marks the drive on force channels (matched).

| Channel | Content | Scale to Excel units |
|---|---|---|
| 0 | boat speed | / 256 (m/s) |
| 1 | boat acceleration | / 256 |
| 2, 3 | unknown, no Excel column matches | |
| 4, 5, 6 | roll, pitch, yaw | / 64 (degrees) |
| 7 on | per sculler: P angle, S angle, P force X, S force X, P force Y, S force Y. Per sweep rower: angle, force X (and force Y where fitted) | / 16 |

Matched blind on one session per boat type (r = 1.0000, 4,000 samples), see [untested boats](../untested-boats.md).
Variations we met (measured): an 8+ with 23 or 27 channels (seats 5 to 8 without force Y); a 1x with a seat
sensor (7 channels per sculler, seat position x 0.25); a 1x with a stretcher force plate (11 channels:
stretcher force X first, then P/S angle, force X, force Y, seat position, stretcher Z, left-right, top-bottom;
stretcher channels use offset 8192, force X and Z / 16, the other two x 0.25). Missing front-seat sensors
leave whole seats empty.

**Force units are not confirmed.** Excel shows raw / 16. Our reader multiplies by 0.5 and calls the result
newtons, a factor chosen so that force x angular velocity matched the device's power. If the Excel value is
kgf, newtons would be raw x 0.613. Nothing in this repository writes force to a FIT file for that reason.

## Stroke rows

Offsets from the row's time stamp (matched):

| Offset | Field | Encoding |
|---|---|---|
| 0 | end of stroke, session ms | `u32` |
| 10 | stroke rate | `u8`, spm = raw x 0.5 - 0.5 |
| 12 | boat speed | `u16`, (raw - 1) / 256 m/s |
| 16 | distance per stroke | `u16`, (raw - 1) / 256 m |
| sculling, per seat +26 | 22: port power, 24: starboard power, 46: total | `u16` |
| sweep, per seat +14 | 22: power, 34: last known power for that seat | `u16` |
| pair | 34 and 36: seat 1 and 2 | `u16` |

**Power = raw - 1001 watts. Raw 0 means "no value"**, which is different from a measured 0 W (raw 1001)
(measured: 64,238 of 64,250 reported values equal Excel; where raw is 0, Excel repeats the previous value).

Row lengths: 1x 48, 2x 80, 2- 56, 4- 80, 8+ 136 (120 when one seat sensor is absent), 4x 144 (guess).

Rows are written when the stroke **ends** (measured: the row comes 60 to 280 ms after the oar angle reaches
its maximum, median per boat; the stroke whose catch lies between two rows belongs to the later row).

The sweep "last known" copy and the sculling total both repeat old values. They are not used.
