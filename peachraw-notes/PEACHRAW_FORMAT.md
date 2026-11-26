# Preliminary `.peachraw` format investigation

This document records what was established from:

- one of our own `.peachraw` files
- the matching `.peach-data` file

The repository utility is `peachraw_inspector.py`. It is intentionally
diagnostic and does not change the existing `.peach-data` reader.

## Confirmed observations

The `.peachraw` file begins with the ASCII magic `PEAC`.

The identifiable prefix fields are little-endian:

| Offset | Type | Observed value | Interpretation |
| ---: | --- | ---: | --- |
| 0 | bytes[4] | `PEAC` | Container magic |
| 4 | u16 | `1` | Format version |
| 6 | u16 | (session number) | Piece/session identifier, matching the filename pair |
| 8 | u16 | `0` | Reserved/unknown |
| 10 | u16 | (logger number) | Athlete/device identifier, matching the filename pair |
| 12 | u16 | `0` | Reserved/unknown |
| 14 | u32 | (redacted) | Timestamp-like value |

The matching `.peach-data` file identifies the same (logger number) and (session number) pair in
its legacy header. Its aperiodic data consists of 18 raw `u16` channels,
which is also the payload width found in `.peachraw` sample records.

Candidate sample records have this layout:

```text
u16 record_type       1
u32 sample_index      monotonically increasing 50 Hz sequence number
u16 raw_channel[18]   offset-binary telemetry values
```

That is 42 bytes per candidate record. The raw values use the same established
offset-binary shape as Peach telemetry, including the common `8191` baseline
and high-bit force encodings seen in `.peach-data` files.

The sample index range in the supplied file reaches approximately 290,513,
which is consistent with roughly 5,810 seconds at 50 Hz and the duration of
the matching `.peach-data` recording.

## Additional framing found

The byte stream is not a plain concatenation of 42-byte records. It contains
additional records interleaved with the sample bytes:

- `PCHRB` and `PCHRE` markers occur in several framing/configuration regions.
- A recurring 12-byte timing marker has a varying four-byte prefix followed by
  the fixed little-endian tail `a0 01 00 00 f4 00 00 00`.
- Recurring six-byte event records begin with `02 0a` and contain a four-byte
  value. Other short event records also occur.

These records can be inserted between channel values, so extracting a sample
by taking 42 bytes directly after every `01 00` signature is not sufficient
for a production decoder. Some stream fragments also contain startup records,
duplicates, and non-chronological sections.

## Conclusion and next step

The format is partially decoded: the container identity, paired IDs, sample
record type, 18-channel width, offset-binary payload shape, and 50 Hz sequence
counter are established. A complete reader still needs a stateful framing
parser that removes the timing/event records, handles the `PCHRB` regions, and
reconstructs a unique chronological sample sequence. These notes stop at
a safe diagnostic boundary so existing `.peach-data` behavior is unaffected.
