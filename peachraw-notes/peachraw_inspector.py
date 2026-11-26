#!/usr/bin/env python3
"""Inspect the container and embedded sample records used by ``.peachraw``.

This is deliberately an investigation utility, not a production telemetry
decoder.  The livestream format contains sample records mixed with timing and
event records, so callers must not treat a byte-offset scan as a complete,
chronologically ordered sample stream yet.

Observed sample record layout::

    u16 record_type       1
    u32 sample_index      50 Hz sequence number
    u16 raw_channel[18]   offset-binary telemetry values

The record is therefore 42 bytes.  The utility reports candidate records and
container markers, leaving the existing ``.peach-data`` reader untouched.
"""

from __future__ import annotations

import argparse
import json
import struct
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


MAGIC = b"PEAC"
SAMPLE_RECORD_TYPE = 1
SAMPLE_CHANNEL_COUNT = 18
SAMPLE_RECORD_SIZE = 2 + 4 + SAMPLE_CHANNEL_COUNT * 2

# A recurring 12-byte timing marker has this fixed tail.  Its first four
# bytes vary per occurrence, so the complete marker is not a fixed byte string.
TIMING_MARKER_TAIL = bytes.fromhex("a0 01 00 00 f4 00 00 00")


@dataclass(frozen=True)
class PeachRawHeader:
    """Fields that are stable and identifiable in the 32-byte prefix."""

    version: int
    piece_id: int
    athlete_id: int
    timestamp_raw: int
    timestamp_utc: str | None


@dataclass(frozen=True)
class SampleCandidate:
    """One candidate 42-byte sample record found by signature scan."""

    offset: int
    sample_index: int
    raw_channels: tuple[int, ...]


def _timestamp_string(value: int) -> str | None:
    try:
        timestamp = datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    if not 2000 <= timestamp.year <= 2040:
        return None
    return timestamp.isoformat()


def parse_header(data: bytes) -> PeachRawHeader:
    """Parse the known fields from a ``PEAC`` file prefix."""

    if len(data) < 18 or data[:4] != MAGIC:
        raise ValueError("not a PEAC/.peachraw file")
    version, piece_id = struct.unpack_from("<HH", data, 4)
    athlete_id = struct.unpack_from("<H", data, 10)[0]
    timestamp_raw = struct.unpack_from("<I", data, 14)[0]
    return PeachRawHeader(
        version=version,
        piece_id=piece_id,
        athlete_id=athlete_id,
        timestamp_raw=timestamp_raw,
        timestamp_utc=_timestamp_string(timestamp_raw),
    )


def iter_sample_candidates(data: bytes) -> Iterator[SampleCandidate]:
    """Yield candidate sample records from an embedded ``.peachraw`` stream.

    The upper bound rejects the large timestamp-like values used by timing
    records while allowing a file containing one 42-byte record per sample.
    Duplicate indices and records from separate stream fragments are retained
    for diagnostics.  This function intentionally makes no claim that the
    candidates are unique or chronologically ordered.
    """

    max_sample_index = len(data) // SAMPLE_RECORD_SIZE + 1
    cursor = 0
    signature = struct.pack("<H", SAMPLE_RECORD_TYPE)
    while True:
        offset = data.find(signature, cursor)
        if offset < 0:
            return
        cursor = offset + 2
        if offset + SAMPLE_RECORD_SIZE > len(data):
            continue
        sample_index = struct.unpack_from("<I", data, offset + 2)[0]
        if sample_index > max_sample_index:
            continue
        raw_channels = struct.unpack_from(
            f"<{SAMPLE_CHANNEL_COUNT}H", data, offset + 6
        )
        yield SampleCandidate(offset, sample_index, raw_channels)


def _find_timing_markers(data: bytes) -> list[int]:
    """Return offsets of timing markers, including their varying u32 prefix."""

    offsets: list[int] = []
    cursor = 0
    while True:
        tail_offset = data.find(TIMING_MARKER_TAIL, cursor)
        if tail_offset < 4:
            if tail_offset < 0:
                break
            cursor = tail_offset + 1
            continue
        offsets.append(tail_offset - 4)
        cursor = tail_offset + len(TIMING_MARKER_TAIL)
    return offsets


def _find_all(data: bytes, needle: bytes) -> list[int]:
    offsets: list[int] = []
    cursor = 0
    while True:
        offset = data.find(needle, cursor)
        if offset < 0:
            return offsets
        offsets.append(offset)
        cursor = offset + 1


def inspect_bytes(data: bytes) -> dict[str, object]:
    """Return compact structural diagnostics for a ``.peachraw`` byte string."""

    header = parse_header(data)
    candidates = list(iter_sample_candidates(data))
    unique_indices = sorted({candidate.sample_index for candidate in candidates})
    unique_index_set = set(unique_indices)
    consecutive_pairs = sum(
        1 for sample_index in unique_indices if sample_index + 1 in unique_index_set
    )
    candidate_counts = Counter(candidate.sample_index for candidate in candidates)
    duplicate_indices = sum(1 for count in candidate_counts.values() if count > 1)
    return {
        "size_bytes": len(data),
        "header": asdict(header),
        "sample_record_size_bytes": SAMPLE_RECORD_SIZE,
        "sample_channel_count": SAMPLE_CHANNEL_COUNT,
        "sample_candidate_count": len(candidates),
        "unique_sample_index_count": len(unique_indices),
        "duplicate_sample_index_count": duplicate_indices,
        "sample_index_range": [unique_indices[0], unique_indices[-1]]
        if unique_indices
        else None,
        "consecutive_sample_index_pairs": consecutive_pairs,
        "timing_marker_count": len(_find_timing_markers(data)),
        "pchr_begin_offsets": _find_all(data, b"PCHRB"),
        "pchr_end_offsets": _find_all(data, b"PCHRE"),
    }


def inspect_file(path: str | Path) -> dict[str, object]:
    with Path(path).open("rb") as handle:
        return inspect_bytes(handle.read())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help=".peachraw file to inspect")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    print(json.dumps(inspect_file(args.file), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
