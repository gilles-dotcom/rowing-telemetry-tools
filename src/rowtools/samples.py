#!/usr/bin/env python3
"""
peach_binary_reader.py, Read 50Hz aperiodic data directly from .peach-data binary files.

Reverse-engineered format for 50Hz data blocks:
  - File contains N one-second blocks of 2736 bytes each
  - Each block: 28-byte header + 27 channels × 50 u16 samples + 8 bytes padding
  - Header: [u32:2736, u32:ref_offset, u32:0, u32:0, u32:timestamp_ms, u32:1000, u32:1]
  - Encoding: value = (raw_u16 - 8191) * scale
  - Scales: rower channels = 1/16, boat speed/accel = 1/256, boat orientation = 1/64

Channel map for 8+ (27 channels):
  ch  0: Speed_Boat        (boat, 1/256)
  ch  1: Accel_Boat         (boat, 1/256)
  ch  2: Unknown_2          (unknown)
  ch  3: Unknown_3          (unknown)
  ch  4: Roll_Boat          (orientation, 1/64)
  ch  5: Pitch_Boat         (orientation, 1/64)
  ch  6: Yaw_Boat           (orientation, 1/64)
  ch  7: GateAngle_1        (rower, 1/16)
  ch  8: GateForceX_1       (rower, 1/16)
  ch  9: GateForceY_1       (rower, 1/16)
  ch 10: GateAngle_2        (rower, 1/16)
  ch 11: Unknown_11         (unknown, expected GateForceX_2)
  ch 12: GateForceY_2       (rower, 1/16)
  ch 13: GateAngle_3        (rower, 1/16)
  ch 14: Unknown_14         (unknown, expected GateForceX_3)
  ch 15: GateForceY_3       (rower, 1/16)
  ch 16: GateAngle_4        (rower, 1/16)
  ch 17: GateForceX_4       (rower, 1/16)
  ch 18: GateForceY_4       (rower, 1/16)
  ch 19: GateAngle_5        (rower, 1/16)
  ch 20: GateForceX_5       (rower, 1/16)
  ch 21: GateAngle_6        (rower, 1/16)
  ch 22: Unknown_22         (unknown, expected GateForceX_6)
  ch 23: GateAngle_7        (rower, 1/16)
  ch 24: GateForceX_7       (rower, 1/16)
  ch 25: GateAngle_8        (rower, 1/16)
  ch 26: GateForceX_8       (rower, 1/16)

GateAngleVel is NOT stored in binary, computed as finite difference of GateAngle.
Seats 1-4 have 3 channels (Angle, ForceX, ForceY); seats 5-8 have 2 (Angle, ForceX).
"""

from __future__ import annotations

import logging
import struct
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────────

BLOCK_SIZE_8PLUS = 2736
HEADER_SIZE = 28
PADDING_SIZE = 8
NUM_CHANNELS_8PLUS = 27
SAMPLES_PER_BLOCK = 50
OFFSET = 8191
STRETCHER_OFFSET = 8192
SAMPLE_RATE_HZ = 50

# Block sizes for different boat types:
#   block_size = HEADER_SIZE + num_channels * SAMPLES_PER_BLOCK * 2 + PADDING_SIZE
#   8+ : 27 channels → 28 + 2700 + 8 = 2736
#   4+ : 19 channels → 28 + 1900 + 8 = 1936
#   4x : 19 channels → 28 + 1900 + 8 = 1936
#   2- : 13 channels → 28 + 1300 + 8 = 1336
#   2x : 13 channels → 28 + 1300 + 8 = 1336
#   1x :  10 channels → 28 + 1000 + 8 = 1036
# The first u32 in each block header IS the block size, so we auto-detect.

# Valid block sizes: (size - 28) must be divisible by 100 (50 samples * 2 bytes)
# giving a valid channel count, OR (size - 28 - 8) divisible by 100 (8-byte padding variant).
# Some firmware versions use 0-byte padding, others use 8-byte padding.
def _is_valid_block_size(size: int) -> bool:
    """Check if a u32 value could be a valid aperiodic block size.

    Known padding variants observed in the wild:
      4  bytes, standard 8+ firmware (2432-byte / 24-channel blocks in our files)
      8  bytes, older firmware variant
     12  bytes, newer firmware (2640-byte blocks, 26 channels, in one of our loggers)
      0  bytes, padding-free variant seen in some early files
    """
    for pad in (0, 4, 8, 12):
        payload = size - HEADER_SIZE - pad
        if payload <= 0 or payload > 10000:
            continue
        if payload % (SAMPLES_PER_BLOCK * 2) != 0:
            continue
        num_ch = payload // (SAMPLES_PER_BLOCK * 2)
        if 1 <= num_ch <= 40:
            return True
    return False

def _channels_from_block_size(block_size: int) -> int:
    """Calculate number of channels from block size."""
    # Try 0-padding, 4-byte, 8-byte, 12-byte padding variants
    for pad in (0, 4, 8, 12):
        payload = block_size - HEADER_SIZE - pad
        if payload > 0 and payload % (SAMPLES_PER_BLOCK * 2) == 0:
            return payload // (SAMPLES_PER_BLOCK * 2)
    # Fallback (shouldn't happen if _is_valid_block_size passed)
    return (block_size - HEADER_SIZE) // (SAMPLES_PER_BLOCK * 2)

# Scale factors by channel type
SCALE_BOAT_SPEED = 1.0 / 256
SCALE_BOAT_ORIENT = 1.0 / 64
SCALE_ROWER  = 1.0 / 16   # Angle channels: raw ÷16 → degrees
SCALE_FORCE  = 0.5         # Force channels: raw ×0.5 → Newtons (verified: 0.5 matches Peach periodic power within 3-10%)
SCALE_SEAT_POSITION = 0.25      # Seat position monitor: raw ×0.25 → PowerLine Seat Posn units
SCALE_STRETCHER_FORCE = 1.0 / 16 # StretcherForceX / Stretcher Z: raw ÷16 → PowerLine units
SCALE_STRETCHER_ROT = 0.25       # Stretcher RL / TB: raw ×0.25 → PowerLine units

# ── 8+ Channel Definition ─────────────────────────────────────────────────────

# Each entry: (channel_index, column_name, scale_factor, is_forcex)
# ForceX channels use bit 15 as a flag (recovery/drive phase indicator)
# that must be cleared before decoding: value = ((raw & 0x7FFF) - 8191) * scale
CHANNEL_MAP_8PLUS = [
    (0,  'Speed_Boat',     SCALE_BOAT_SPEED,  False),
    (1,  'Accel_Boat',     SCALE_BOAT_SPEED,  False),
    (2,  'Unknown_2',      None,              False),  # Unknown encoding (possibly GPS-related)
    (3,  'Unknown_3',      None,              False),  # Unknown encoding (possibly GPS-related)
    (4,  'Roll_Boat',      SCALE_BOAT_ORIENT, False),
    (5,  'Pitch_Boat',     SCALE_BOAT_ORIENT, False),
    (6,  'Yaw_Boat',       SCALE_BOAT_ORIENT, False),
    (7,  'GateAngle_1',    SCALE_ROWER,       False),
    (8,  'GateForceX_1',   SCALE_FORCE,       True),
    (9,  'GateForceY_1',   SCALE_FORCE,       False),
    (10, 'GateAngle_2',    SCALE_ROWER,       False),
    (11, 'GateForceX_2',   SCALE_FORCE,       True),
    (12, 'GateForceY_2',   SCALE_FORCE,       False),
    (13, 'GateAngle_3',    SCALE_ROWER,       False),
    (14, 'GateForceX_3',   SCALE_FORCE,       True),
    (15, 'GateForceY_3',   SCALE_FORCE,       False),
    (16, 'GateAngle_4',    SCALE_ROWER,       False),
    (17, 'GateForceX_4',   SCALE_FORCE,       True),
    (18, 'GateForceY_4',   SCALE_FORCE,       False),
    (19, 'GateAngle_5',    SCALE_ROWER,       False),
    (20, 'GateForceX_5',   SCALE_FORCE,       True),
    (21, 'GateAngle_6',    SCALE_ROWER,       False),
    (22, 'GateForceX_6',   SCALE_FORCE,       True),
    (23, 'GateAngle_7',    SCALE_ROWER,       False),
    (24, 'GateForceX_7',   SCALE_FORCE,       True),
    (25, 'GateAngle_8',    SCALE_ROWER,       False),
    (26, 'GateForceX_8',   SCALE_FORCE,       True),
]

# ── Channel maps for other boat types ────────────────────────────────────────
# The first 7 channels (boat telemetry) are always the same.
# After that, each rower gets 2-3 channels depending on position.
# Pattern: seats 1-4 get (Angle, ForceX, ForceY), seats 5-8 get (Angle, ForceX).
# For smaller boats, the same pattern applies with fewer rowers.

BOAT_CHANNELS_PREFIX = [
    (0,  'Speed_Boat',     SCALE_BOAT_SPEED,  False),
    (1,  'Accel_Boat',     SCALE_BOAT_SPEED,  False),
    (2,  'Unknown_2',      None,              False),
    (3,  'Unknown_3',      None,              False),
    (4,  'Roll_Boat',      SCALE_BOAT_ORIENT, False),
    (5,  'Pitch_Boat',     SCALE_BOAT_ORIENT, False),
    (6,  'Yaw_Boat',       SCALE_BOAT_ORIENT, False),
]


def _is_sculling(num_channels: int, boat_type: str) -> bool:
    """Detect if this is a sculling boat (2 oars per rower → 6 ch/seat)."""
    bt = boat_type.lower().strip()
    if bt in ('1x', '2x', '4x'):
        return True
    if bt in ('2-', '2', '4-', '4', '8', '8+'):
        return False
    # Auto-detect from channel count:  sculling = 6 ch/seat, sweep = 3 ch/seat
    rower_ch = num_channels - 7
    if rower_ch > 0 and rower_ch % 6 == 0:
        return True
    return False


def _build_channel_map(num_channels: int, boat_type: str = 'auto') -> list:
    """
    Build a channel map given the detected number of channels.
    Returns list of (ch_idx, name, scale, is_forcex) tuples.

    For known boat types, uses the known map. Otherwise infers from
    channel count: rower_channels = num_channels - 7 (boat prefix).

    Sculling boats have 6 channels per seat (P/S × Angle, ForceX, ForceY).
    Channel order per seat: P_Angle, S_Angle, P_ForceX, S_ForceX, P_ForceY, S_ForceY.
    We output them as e.g. GateAngle_1 (=port), S_GateAngle_1 (=strokeside).
    """
    if num_channels == 27:
        return CHANNEL_MAP_8PLUS  # Known 8+ map (sweep)

    # Start with the 7 boat channels
    ch_map = list(BOAT_CHANNELS_PREFIX)
    rower_channels = num_channels - 7
    if rower_channels <= 0:
        return ch_map

    ch_idx = 7
    sculling = _is_sculling(num_channels, boat_type)

    if sculling and rower_channels == 11 and boat_type.lower().strip() == '1x':
        # 1x with stretcher force plate + Peach seat-position monitor.
        # PowerLine order:
        # StretcherForceX, P/S angle, P/S ForceX, P/S ForceY,
        # SeatPosn, Stretcher Z, Stretcher RL, Stretcher TB.
        ch_map.append((ch_idx, 'StretcherForceX_1', SCALE_STRETCHER_FORCE, False))
        ch_map.append((ch_idx + 1, 'GateAngle_1', SCALE_ROWER, False))
        ch_map.append((ch_idx + 2, 'S_GateAngle_1', SCALE_ROWER, False))
        ch_map.append((ch_idx + 3, 'GateForceX_1', SCALE_FORCE, True))
        ch_map.append((ch_idx + 4, 'S_GateForceX_1', SCALE_FORCE, True))
        ch_map.append((ch_idx + 5, 'GateForceY_1', SCALE_FORCE, False))
        ch_map.append((ch_idx + 6, 'S_GateForceY_1', SCALE_FORCE, False))
        ch_map.append((ch_idx + 7, 'SeatPosn_1', SCALE_SEAT_POSITION, False))
        ch_map.append((ch_idx + 8, 'StretcherZ_1', SCALE_STRETCHER_FORCE, False))
        ch_map.append((ch_idx + 9, 'StretcherRL_1', SCALE_STRETCHER_ROT, False))
        ch_map.append((ch_idx + 10, 'StretcherTB_1', SCALE_STRETCHER_ROT, False))
    elif sculling and rower_channels == 7 and boat_type.lower().strip() == '1x':
        # 1x with Peach seat-position monitor.
        ch_map.append((ch_idx, 'GateAngle_1', SCALE_ROWER, False))       # Port angle
        ch_map.append((ch_idx + 1, 'S_GateAngle_1', SCALE_ROWER, False)) # Stbd angle
        ch_map.append((ch_idx + 2, 'GateForceX_1', SCALE_FORCE, True))   # Port ForceX
        ch_map.append((ch_idx + 3, 'S_GateForceX_1', SCALE_FORCE, True)) # Stbd ForceX
        ch_map.append((ch_idx + 4, 'GateForceY_1', SCALE_FORCE, False))  # Port ForceY
        ch_map.append((ch_idx + 5, 'S_GateForceY_1', SCALE_FORCE, False))# Stbd ForceY
        ch_map.append((ch_idx + 6, 'SeatPosn_1', SCALE_SEAT_POSITION, False))
    elif sculling and rower_channels % 6 == 0:
        # Sculling: 6 channels per seat
        # Order: P_Angle, S_Angle, P_ForceX, S_ForceX, P_ForceY, S_ForceY
        n_seats = rower_channels // 6
        for s in range(1, n_seats + 1):
            ch_map.append((ch_idx,     f'GateAngle_{s}',    SCALE_ROWER,  False))   # Port angle
            ch_map.append((ch_idx + 1, f'S_GateAngle_{s}',  SCALE_ROWER,  False))   # Stbd angle
            ch_map.append((ch_idx + 2, f'GateForceX_{s}',   SCALE_FORCE,  True))    # Port ForceX
            ch_map.append((ch_idx + 3, f'S_GateForceX_{s}', SCALE_FORCE,  True))    # Stbd ForceX
            ch_map.append((ch_idx + 4, f'GateForceY_{s}',   SCALE_FORCE,  False))   # Port ForceY
            ch_map.append((ch_idx + 5, f'S_GateForceY_{s}', SCALE_FORCE,  False))   # Stbd ForceY
            ch_idx += 6
    elif rower_channels % 3 == 0:
        # Sweep: 3 channels per rower (Angle, ForceX, ForceY)
        n_rowers = rower_channels // 3
        for s in range(1, n_rowers + 1):
            ch_map.append((ch_idx,     f'GateAngle_{s}',  SCALE_ROWER,  False))
            ch_map.append((ch_idx + 1, f'GateForceX_{s}', SCALE_FORCE,  True))
            ch_map.append((ch_idx + 2, f'GateForceY_{s}', SCALE_FORCE,  False))
            ch_idx += 3
    elif rower_channels % 2 == 0:
        # 2 channels per rower (Angle, ForceX only, seats 5-8 in 8+)
        n_rowers = rower_channels // 2
        for s in range(1, n_rowers + 1):
            ch_map.append((ch_idx,     f'GateAngle_{s}',  SCALE_ROWER,  False))
            ch_map.append((ch_idx + 1, f'GateForceX_{s}', SCALE_FORCE,  True))
            ch_idx += 2
    else:
        # Mixed layout like 8+ (some 3ch, some 2ch), assign generically
        for i in range(rower_channels):
            ch_map.append((ch_idx + i, f'Channel_{ch_idx + i}', SCALE_ROWER, False))

    return ch_map


# Columns that session_analyzer expects (matching Excel Aperiodic sheet)
EXCEL_COMPATIBLE_COLUMNS = [
    'Time',
    'GateAngle_1', 'GateAngle_2', 'GateAngle_3', 'GateAngle_4',
    'GateAngle_5', 'GateAngle_6', 'GateAngle_7', 'GateAngle_8',
    'GateForceX_1', 'GateForceX_2', 'GateForceX_3', 'GateForceX_4',
    'GateForceX_5', 'GateForceX_6', 'GateForceX_7', 'GateForceX_8',
    'GateForceY_1', 'GateForceY_2', 'GateForceY_3', 'GateForceY_4',
    'Speed_Boat', 'Distance_Boat', 'Accel_Boat',
    'Roll_Boat', 'Pitch_Boat', 'Yaw_Boat',
    'GateAngleVel_1', 'GateAngleVel_2', 'GateAngleVel_3', 'GateAngleVel_4',
    'GateAngleVel_5', 'GateAngleVel_6', 'GateAngleVel_7', 'GateAngleVel_8',
    'SeatPosn_1', 'SeatPosnVel_1',
    'StretcherForceX_1', 'StretcherZ_1', 'StretcherRL_1', 'StretcherTB_1',
]


# ── Block Scanner ──────────────────────────────────────────────────────────────

def _find_aperiodic_blocks(data: bytes, expected_block_size: int = 0) -> tuple[list[int], int]:
    """
    Scan the file for aperiodic data blocks of any valid size.
    Returns (list_of_byte_offsets, detected_block_size).

    Blocks are identified by the header signature:
      [u32:block_size, u32:*, u32:*, u32:*, u32:timestamp_ms, u32:1000, u32:1]
    where block_size is a structurally valid aperiodic block size.

    If expected_block_size is given (>0), only looks for that size.
    Otherwise auto-detects from the first matching block.
    """
    _log = logging.getLogger(__name__)

    n = len(data)
    offsets = []
    detected_size = expected_block_size

    pos = 0
    min_block = 100  # smallest conceivable block
    while pos + min_block <= n:
        marker = struct.unpack_from('<I', data, pos)[0]

        # Check if this marker is a valid block size
        if _is_valid_block_size(marker):
            # Make sure we can read the full header
            if pos + 28 <= n:
                v5, v6 = struct.unpack_from('<II', data, pos + 20)
                if 500 <= v5 <= 1100 and v6 == 1:
                    # If we haven't locked in a block size yet, use this one
                    if detected_size == 0:
                        detected_size = marker
                        _log.info("Auto-detected aperiodic block size: %d bytes (%d channels)",
                                  detected_size, _channels_from_block_size(detected_size))

                    # Only accept blocks matching detected size
                    if marker == detected_size and pos + detected_size <= n:
                        offsets.append(pos)
                        pos += detected_size
                        continue

        pos += 4  # Scan in 4-byte steps

    if not offsets:
        # Provide diagnostic info for debugging
        _log.error("No aperiodic data blocks found in %d-byte file.", n)
        # Scan first few KB for any recognizable patterns
        diag_markers = {}
        for dpos in range(0, min(n - 4, 100000), 4):
            m = struct.unpack_from('<I', data, dpos)[0]
            if _is_valid_block_size(m):
                diag_markers.setdefault(m, 0)
                diag_markers[m] += 1
        if diag_markers:
            _log.error("  Candidate block-size markers found: %s", diag_markers)
        else:
            _log.error("  No candidate block-size markers found in first 100KB.")
        # Also log first 64 bytes as hex for manual inspection
        _log.error("  File header (hex): %s", data[:64].hex())

        raise ValueError(
            f"No aperiodic data blocks found in file ({n} bytes). "
            f"Candidate markers: {diag_markers if diag_markers else 'none'}"
        )

    return offsets, detected_size


def _decode_block(data: bytes, offset: int, num_channels: int) -> tuple[int, np.ndarray]:
    """
    Decode a single aperiodic data block.
    Returns (timestamp_ms, raw_u16_array[num_channels, 50]).
    """
    ts = struct.unpack_from('<I', data, offset + 16)[0]

    # Read all channel data as u16 array
    data_start = offset + HEADER_SIZE
    raw = np.frombuffer(data, dtype='<u2', count=num_channels * SAMPLES_PER_BLOCK,
                        offset=data_start)
    raw = raw.reshape(num_channels, SAMPLES_PER_BLOCK)

    return ts, raw


def _is_active_block(raw: np.ndarray, channel_map: list) -> bool:
    """
    Check if a block contains actual rowing data (not idle/inactive).
    An inactive block typically has all values at or near the offset (8191).
    Check every angle channel, partial-crew sessions can have dead front seats
    while later seats still contain valid rowing data.
    """
    angle_channels = [(ch_idx, name) for ch_idx, name, scale, _is_fx in channel_map
                      if name.startswith('GateAngle_') or name.startswith('S_GateAngle_')]
    for ch_idx, name in angle_channels:
        if float(np.std(raw[ch_idx].astype(np.float64))) > 5.0:
            return True
    # No angle channels found → assume active; all checked channels flat → inactive
    return len(angle_channels) == 0


def _detect_rower_channel_layout(data: bytes, block_offsets: list,
                                  num_channels: int) -> list:
    """
    Auto-detect the rower channel layout by sampling raw blocks and checking
    which channels have bit 15 set frequently (ForceX phase flag).

    ForceX channels are identified purely by signal: the Peach firmware sets
    bit 15 on ForceX values during the RECOVERY phase (~65% of samples).
    Bit 15 = 0 during drive (high force), bit 15 = 1 during recovery (low/negative).
    Angle and ForceY channels never have bit 15 set.
    This binary gap (0% vs ~65%) means a threshold of 30% has very wide
    margin, making the classification robust to any number of ForceY channels
    and to partial-crew sessions where some rowers join late.

    ForceY detection is positional: after identifying all ForceX channels,
    we scan seat groups left-to-right from ch 7 using the rule:
      - Non-FX channel:  Angle of current seat
      - FX channel:      ForceX of current seat
      - Non-FX immediately after FX:
            * If the channel after it is ALSO non-FX (or we're at the end of
              channels): this is ForceY of the current seat.
            * If the channel after it IS ForceX: this is the Angle of the
              NEXT seat (no ForceY for this seat).

    Sampling strategy:
      - Always include the first and last 50 blocks so rowers who start or
        stop at the extremes of the session are captured.
      - Fill the remainder with evenly-spaced blocks from the middle.
      - Cap at MAX_BLOCKS total to stay fast on very long sessions.
    The adaptive MIN_SAMPLES threshold scales with session length so it is
    tight enough to reject noise on long files but permissive on short ones.

    Returns channel map as list of (ch_idx, name, scale, is_forcex).
    Falls back to _build_channel_map if sampling fails or is ambiguous.
    """
    _log = logging.getLogger(__name__)

    n_blocks = len(block_offsets)
    if n_blocks == 0:
        return _build_channel_map(num_channels)

    # ── Sampling: always cover both ends + evenly-spaced middle ───────────────
    MAX_BLOCKS = 400
    if n_blocks <= MAX_BLOCKS:
        # Small file, use every block
        indices = list(range(n_blocks))
    else:
        # Anchor on the first and last 50 blocks, fill rest from the middle
        ANCHOR = min(50, n_blocks // 4)
        heads = list(range(ANCHOR))
        tails = list(range(n_blocks - ANCHOR, n_blocks))
        n_mid = MAX_BLOCKS - 2 * ANCHOR
        if n_mid > 0:
            mids = list(np.linspace(ANCHOR, n_blocks - ANCHOR - 1,
                                    n_mid, dtype=int))
        else:
            mids = []
        indices = sorted(set(heads + mids + tails))

    bit15_counts   = np.zeros(num_channels, dtype=np.int64)
    nonzero_counts = np.zeros(num_channels, dtype=np.int64)

    for i in indices:
        offset = block_offsets[i]
        data_start = offset + HEADER_SIZE
        try:
            raw = np.frombuffer(data, dtype='<u2',
                                count=num_channels * SAMPLES_PER_BLOCK,
                                offset=data_start).reshape(num_channels, SAMPLES_PER_BLOCK)
        except ValueError:
            continue
        for c in range(num_channels):
            ch = raw[c]
            nz_mask = ch != 0
            nonzero_counts[c] += int(nz_mask.sum())
            bit15_counts[c]   += int((ch[nz_mask] >= 32768).sum())

    # ── ForceX classification ──────────────────────────────────────────────────
    # Adaptive threshold: 1 % of the maximum non-zero count seen across rower
    # channels, floored at 20 samples.  This prevents spurious classification
    # on completely inactive channels while remaining generous on short files.
    max_nz = max(int(nonzero_counts[7:].max()) if num_channels > 7 else 0, 1)
    MIN_SAMPLES = max(20, max_nz // 100)

    is_forcex_ch = np.zeros(num_channels, dtype=bool)
    for c in range(7, num_channels):
        if nonzero_counts[c] >= MIN_SAMPLES:
            frac = bit15_counts[c] / nonzero_counts[c]
            is_forcex_ch[c] = frac > 0.30

    # Need at least 1 detected ForceX channel to trust the detection
    n_forcex = int(is_forcex_ch[7:].sum())
    if n_forcex == 0:
        _log.warning("_detect_rower_channel_layout: no ForceX channels detected "
                     "(sampled %d/%d blocks, MIN_SAMPLES=%d) "
                     "- falling back to _build_channel_map",
                     len(indices), n_blocks, MIN_SAMPLES)
        return _build_channel_map(num_channels)

    # Log a compact summary: channel index → role
    roles = {}
    for c in range(7, num_channels):
        if is_forcex_ch[c]:
            roles[c] = "FX"
        elif nonzero_counts[c] >= MIN_SAMPLES:
            roles[c] = "A/FY"
        else:
            roles[c] = "inact"
    _log.info("_detect_rower_channel_layout: %d ForceX out of %d rower channels  "
              "(sampled %d/%d blocks, MIN_SAMPLES=%d)  %s",
              n_forcex, num_channels - 7, len(indices), n_blocks, MIN_SAMPLES,
              {c: r for c, r in roles.items()})

    # ── Build seat groups left-to-right from ch 7 ─────────────────────────────
    ch_map = list(BOAT_CHANNELS_PREFIX)
    seat = 1
    c = 7  # first rower channel

    while c < num_channels:
        if is_forcex_ch[c]:
            # Orphan ForceX (no preceding Angle), rare, treat conservatively
            ch_map.append((c, f'GateForceX_{seat}', SCALE_FORCE, True))
            c += 1
            seat += 1
            continue

        # Non-ForceX: this is the Angle of the current seat
        ch_map.append((c, f'GateAngle_{seat}', SCALE_ROWER, False))
        c += 1

        # Look for ForceX immediately after the Angle
        if c < num_channels and is_forcex_ch[c]:
            ch_map.append((c, f'GateForceX_{seat}', SCALE_FORCE, True))
            c += 1

            # After ForceX: ForceY or the next seat's Angle?
            # Rule: if the following non-FX channel is itself followed by
            # ANOTHER non-FX (or the file ends), it is ForceY for this seat.
            # If it is followed by a FX, it is the next seat's Angle.
            if c < num_channels and not is_forcex_ch[c]:
                at_end     = (c + 1 >= num_channels)
                next_is_fx = (not at_end) and is_forcex_ch[c + 1]
                if at_end or not next_is_fx:
                    ch_map.append((c, f'GateForceY_{seat}', SCALE_FORCE, False))
                    c += 1
                # else: non-FX channel is the next seat's Angle, leave c unchanged

        seat += 1

    return ch_map


# ── Main Reader Class ─────────────────────────────────────────────────────────

class PeachBinaryReader:
    """
    Reads 50Hz aperiodic data from .peach-data binary files.

    Usage:
        reader = PeachBinaryReader('session.peach-data')
        # Get full session as DataFrame-compatible dict
        df = reader.to_dataframe()
        # Or get just a time segment (ms)
        df = reader.to_dataframe(start_ms=3000000, end_ms=3060000)
    """

    def __init__(self, path: str | Path, boat_type: str = '8'):
        self._log = logging.getLogger(__name__)

        self.path = Path(path)
        self._log.info("PeachBinaryReader: opening %s (%s)", self.path.name, self.path)
        self.data = self.path.read_bytes()
        self.boat_type = boat_type
        self._log.info("  File size: %d bytes, boat_type hint: %s", len(self.data), boat_type)

        # Extract session start UTC from file header (offset 24)
        if len(self.data) >= 28:
            raw_ts = struct.unpack_from('<I', self.data, 24)[0]
            try:
                start_utc = datetime.fromtimestamp(raw_ts, timezone.utc)
                self.start_utc = start_utc if 2005 <= start_utc.year <= 2040 else None
            except (OSError, ValueError):
                self.start_utc = None
        else:
            self.start_utc = None

        # Find all aperiodic data blocks, auto-detects block size
        self._block_offsets, self._block_size = _find_aperiodic_blocks(self.data)
        self._num_blocks = len(self._block_offsets)
        self._num_channels = _channels_from_block_size(self._block_size)
        self._log.info("  Found %d blocks, block_size=%d, channels=%d",
                       self._num_blocks, self._block_size, self._num_channels)

        # Build channel map.
        # Sculling boats (1x/2x/4x) may store INTERLEAVED port+strokeside channels
        # per seat (P_Angle, S_Angle, P_ForceX, S_ForceX, P_ForceY, S_ForceY = 6 ch/sculler).
        # The bit-15 auto-detector doesn't know about this layout and would misidentify
        # the two consecutive Angle channels (P/S) as two separate sweep seats.
        # Detect this case: rower_ch == 6 * expected_scullers → use static sculling map.
        # A 1x with seat-position monitor appends one SeatPosn channel.
        _SCULLING_CREW: dict[str, int] = {'1x': 1, '2x': 2, '4x': 4}
        _bt = boat_type.lower().strip()
        _expected_scullers = _SCULLING_CREW.get(_bt)
        _rower_ch = self._num_channels - 7
        _use_sculling_map = (
            _expected_scullers is not None
            and _rower_ch > 0
            and (
                _rower_ch == 6 * _expected_scullers   # 6 ch/sculler → interleaved P/S
                or (_bt == '1x' and _rower_ch == 7)   # + SeatPosn
                or (_bt == '1x' and _rower_ch == 11)  # + SeatPosn + stretcher force plate
            )
        )

        if self._num_channels > 7 and self._num_blocks > 0:
            if _use_sculling_map:
                # Interleaved P/S format: static map correctly assigns S_ channel names
                self._log.info(
                    "PeachBinaryReader: sculling interleaved P/S format detected "
                    "(%d rower ch, %d scullers), using static channel map",
                    _rower_ch, _expected_scullers)
                self._channel_map = _build_channel_map(self._num_channels, boat_type)
            else:
                # Sweep or per-rower sculling, auto-detect from bit-15 sampling
                self._channel_map = _detect_rower_channel_layout(
                    self.data, self._block_offsets, self._num_channels)
        else:
            self._channel_map = _build_channel_map(self._num_channels, boat_type)

        # Build timestamp index (block_index → timestamp_ms)
        self._timestamps = np.zeros(self._num_blocks, dtype=np.uint32)
        for i, off in enumerate(self._block_offsets):
            self._timestamps[i] = struct.unpack_from('<I', self.data, off + 16)[0]
        active = self._timestamps[self._timestamps > 0].astype(np.int64)
        self._timestamps_monotonic = len(active) < 2 or bool(np.all(np.diff(active) >= 0))

    def close(self) -> None:
        """Release large buffers. Safe to call multiple times."""
        self.data = b''
        self._block_offsets = []
        self._timestamps = np.zeros(0, dtype=np.uint32)
        self._timestamps_monotonic = True
        self._channel_map = {}
        self._num_blocks = 0

    @property
    def num_blocks(self) -> int:
        return self._num_blocks

    @property
    def duration_ms(self) -> int:
        """Total duration in ms based on timestamps."""
        active = self._timestamps[self._timestamps > 0]
        if len(active) < 2:
            return 0
        active_i64 = active.astype(np.int64)
        if not self._timestamps_monotonic:
            return max(0, int(np.max(active_i64) - np.min(active_i64))) + 1000
        return max(0, int(active_i64[-1] - active_i64[0])) + 1000  # +1s for last block

    @property
    def time_range_ms(self) -> tuple[int, int]:
        """(first_timestamp_ms, last_timestamp_ms) of active blocks."""
        active = self._timestamps[self._timestamps > 0]
        if len(active) == 0:
            return (0, 0)
        active_i64 = active.astype(np.int64)
        if not self._timestamps_monotonic:
            return (int(np.min(active_i64)), int(np.max(active_i64)))
        return (int(active_i64[0]), int(active_i64[-1]))

    def _block_range_for_time(self, start_ms: Optional[int], end_ms: Optional[int]) -> tuple[int, int]:
        """
        Return (first_block_idx, last_block_idx_exclusive) for the given time range.
        If start_ms/end_ms are None, returns full range.
        """
        if start_ms is None and end_ms is None:
            return 0, self._num_blocks

        if not self._timestamps_monotonic:
            ts = self._timestamps.astype(np.int64)
            mask = ts > 0
            if start_ms is not None:
                mask &= ts >= int(start_ms)
            if end_ms is not None:
                mask &= ts <= int(end_ms)
            idxs = np.flatnonzero(mask)
            if len(idxs) == 0:
                return 0, 0
            return max(0, int(idxs[0]) - 1), min(self._num_blocks, int(idxs[-1]) + 2)

        first = 0
        last = self._num_blocks

        if start_ms is not None:
            # Find first block with timestamp >= start_ms
            # Go 1 block earlier to avoid missing boundary strokes
            idx = np.searchsorted(self._timestamps, start_ms, side='left')
            first = max(0, idx - 1)

        if end_ms is not None:
            # Find last block with timestamp <= end_ms
            # Go 1 block later to include boundary
            idx = np.searchsorted(self._timestamps, end_ms, side='right')
            last = min(self._num_blocks, idx + 1)

        return first, last

    def read_raw_blocks(self, start_ms: Optional[int] = None,
                        end_ms: Optional[int] = None,
                        skip_inactive: bool = True) -> tuple[np.ndarray, np.ndarray]:
        """
        Read raw u16 data for the given time range.

        Returns:
            timestamps: 1D array of per-sample timestamps in ms (float64)
            raw_data: 2D array [n_samples, n_channels] of u16 values
        """
        first, last = self._block_range_for_time(start_ms, end_ms)
        n_blocks = last - first

        all_timestamps = []
        all_raw = []

        for i in range(first, last):
            offset = self._block_offsets[i]
            ts, raw = _decode_block(self.data, offset, self._num_channels)

            if ts == 0:
                continue  # Skip zero-timestamp blocks

            if skip_inactive and not _is_active_block(raw, self._channel_map):
                continue

            # Generate 50 sub-timestamps within this 1-second block
            # Each sample is 20ms apart (50Hz)
            block_times = ts + np.arange(SAMPLES_PER_BLOCK) * (1000.0 / SAMPLE_RATE_HZ)
            all_timestamps.append(block_times)
            all_raw.append(raw.T)  # Transpose to [50, num_channels]

        if not all_timestamps:
            return np.array([], dtype=np.float64), np.array([], dtype=np.uint16).reshape(0, self._num_channels)

        timestamps = np.concatenate(all_timestamps)
        raw_data = np.concatenate(all_raw, axis=0)

        # Apply time filter at sample level
        if start_ms is not None or end_ms is not None:
            mask = np.ones(len(timestamps), dtype=bool)
            if start_ms is not None:
                mask &= timestamps >= start_ms
            if end_ms is not None:
                mask &= timestamps <= end_ms
            timestamps = timestamps[mask]
            raw_data = raw_data[mask]

        return timestamps, raw_data

    def _decode_channels(self, raw_data: np.ndarray) -> dict[str, np.ndarray]:
        """
        Decode raw u16 data into physical values using channel map.
        Returns dict of column_name → float64 array.

        Bit 15 is used as a drive/recovery phase flag by some firmware versions
        and must always be cleared before applying the standard offset-binary
        decode, for ALL rower channels (angles and forces alike).
        """
        result = {}
        for ch_idx, name, scale, is_forcex in self._channel_map:
            if scale is None:
                # Unknown channel, store raw values for debugging
                result[name] = raw_data[:, ch_idx].astype(np.float64)
                continue

            raw_ch = raw_data[:, ch_idx]
            # Always clear bit 15, some firmware variants set it on angle
            # channels (not just ForceX), causing a spurious +2048° offset.
            offset = STRETCHER_OFFSET if name.startswith('Stretcher') else OFFSET
            values = ((raw_ch & 0x7FFF).astype(np.float64) - offset) * scale
            # raw=0 is a sentinel meaning "no data", it decodes to a physically
            # impossible value (≈ −512 ° or −512 N).  Mask it as NaN so that
            # stroke-segmentation and analysis code never sees the spike.
            values[raw_ch == 0] = np.nan
            # Hardware out-of-range sentinel (raw 0x5FFF decodes to 16384).
            # Clamp force channels to physically possible range.
            if is_forcex:
                values[(values > 2000.0) | (values < -1000.0)] = np.nan
            result[name] = values

        return result

    def _compute_derived(self, columns: dict[str, np.ndarray],
                         timestamps: np.ndarray) -> dict[str, np.ndarray]:
        """
        Compute derived columns that aren't stored in binary:
        - GateAngleVel (finite difference of GateAngle, deg/s)
        - Distance_Boat (cumulative integration of Speed)
        - NormalizedTime (not computed here, done per-stroke in analyzer)
        """
        dt = 1.0 / SAMPLE_RATE_HZ  # 0.02s

        # GateAngleVel for each seat (central difference)
        # Also compute S_ (strokeside) variants for sculling boats
        for seat in range(1, 9):
            for prefix in ('', 'S_'):
                angle_col = f'{prefix}GateAngle_{seat}'
                vel_col = f'{prefix}GateAngleVel_{seat}'
                if angle_col in columns:
                    angle = columns[angle_col]
                    vel = np.zeros_like(angle)
                    if len(angle) > 2:
                        vel[1:-1] = (angle[2:] - angle[:-2]) / (2 * dt)
                        vel[0] = (angle[1] - angle[0]) / dt
                        vel[-1] = (angle[-1] - angle[-2]) / dt
                    columns[vel_col] = vel

        # Seat position velocity (same central-difference convention as angle velocity).
        for seat in range(1, 9):
            pos_col = f'SeatPosn_{seat}'
            vel_col = f'SeatPosnVel_{seat}'
            if pos_col in columns:
                pos = columns[pos_col]
                vel = np.zeros_like(pos)
                if len(pos) > 2:
                    vel[1:-1] = (pos[2:] - pos[:-2]) / (2 * dt)
                    vel[0] = (pos[1] - pos[0]) / dt
                    vel[-1] = (pos[-1] - pos[-2]) / dt
                columns[vel_col] = vel

        # Distance_Boat (cumulative trapezoid integration of Speed)
        # NaN-safe: replace NaN speed samples with 0 before integrating so that
        # a single bad GPS sample doesn't wipe out the entire distance trace.
        if 'Speed_Boat' in columns:
            speed = columns['Speed_Boat']
            speed_clean = np.where(np.isnan(speed), 0.0, speed)
            dist = np.zeros_like(speed_clean)
            if len(speed_clean) > 1:
                dist[1:] = np.cumsum(0.5 * (speed_clean[1:] + speed_clean[:-1]) * dt)
            columns['Distance_Boat'] = dist

        return columns

    def to_dict(self, start_ms: Optional[int] = None,
                end_ms: Optional[int] = None,
                skip_inactive: bool = True,
                include_unknown: bool = False,
                compute_derived: bool = True) -> dict[str, np.ndarray]:
        """
        Read and decode data, returning a dict of column_name → numpy array.

        Args:
            start_ms: Start time in session-ms (None = from beginning)
            end_ms: End time in session-ms (None = to end)
            skip_inactive: Skip blocks where rowers aren't active
            include_unknown: Include unknown channels (Unknown_2, etc.)
            compute_derived: Compute GateAngleVel, Distance_Boat

        Returns:
            Dict with 'Time' + decoded channel columns as numpy arrays
        """
        timestamps, raw_data = self.read_raw_blocks(start_ms, end_ms, skip_inactive)

        if len(timestamps) == 0:
            return {'Time': np.array([], dtype=np.float64)}

        columns = self._decode_channels(raw_data)

        if compute_derived:
            columns = self._compute_derived(columns, timestamps)

        # Remove unknown channels unless requested
        if not include_unknown:
            columns = {k: v for k, v in columns.items() if not k.startswith('Unknown_')}

        columns['Time'] = timestamps
        return columns

    def to_dataframe(self, start_ms: Optional[int] = None,
                     end_ms: Optional[int] = None,
                     skip_inactive: bool = True,
                     include_unknown: bool = False,
                     compute_derived: bool = True):
        """
        Read and decode data, returning a pandas DataFrame.
        Compatible with session_analyzer.py's expected column format.

        Args: same as to_dict()
        Returns: pandas DataFrame with columns matching Excel Aperiodic sheet
        """
        import pandas as pd

        data = self.to_dict(start_ms, end_ms, skip_inactive, include_unknown, compute_derived)

        if len(data.get('Time', [])) == 0:
            return pd.DataFrame(columns=EXCEL_COMPATIBLE_COLUMNS)

        df = pd.DataFrame(data)

        # Reorder columns to match Excel format (Time first, then known columns)
        ordered_cols = ['Time']
        for col in EXCEL_COMPATIBLE_COLUMNS[1:]:
            if col in df.columns:
                ordered_cols.append(col)

        # Add any extra columns not in the standard list
        for col in df.columns:
            if col not in ordered_cols:
                ordered_cols.append(col)

        return df[[c for c in ordered_cols if c in df.columns]]

    def summary(self) -> dict:
        """Return a summary of the file contents."""
        active = self._timestamps[self._timestamps > 0]
        t_min, t_max = (int(active[0]), int(active[-1])) if len(active) > 0 else (0, 0)

        return {
            'file': str(self.path),
            'file_size_mb': len(self.data) / 1e6,
            'boat_type': self.boat_type,
            'block_size': self._block_size,
            'num_channels': self._num_channels,
            'start_utc': str(self.start_utc) if self.start_utc else None,
            'num_blocks': self._num_blocks,
            'active_blocks': int(np.sum(self._timestamps > 0)),
            'time_range_ms': (t_min, t_max),
            'duration_s': (t_max - t_min) / 1000 if t_max > t_min else 0,
            'total_samples_est': int(np.sum(self._timestamps > 0)) * SAMPLES_PER_BLOCK,
            'first_block_hex': hex(self._block_offsets[0]) if self._block_offsets else '0x0',
            'channel_names': [name for _, name, _, _ in self._channel_map],
        }


# ── Standalone CLI ─────────────────────────────────────────────────────────────

def main():
    import argparse
    import time

    parser = argparse.ArgumentParser(description='Read 50Hz data from .peach-data binary files')
    parser.add_argument('file', help='.peach-data file path')
    parser.add_argument('--boat', default='8', help='Boat type (default: 8)')
    parser.add_argument('--start', type=int, default=None, help='Start time in ms')
    parser.add_argument('--end', type=int, default=None, help='End time in ms')
    parser.add_argument('--info', action='store_true', help='Print file summary only')
    parser.add_argument('--parquet', type=str, default=None, help='Output to parquet file')
    parser.add_argument('--csv', type=str, default=None, help='Output to CSV file')
    parser.add_argument('--benchmark', action='store_true', help='Benchmark read performance')

    args = parser.parse_args()

    reader = PeachBinaryReader(args.file, boat_type=args.boat)

    if args.info:
        info = reader.summary()
        for k, v in info.items():
            print(f"  {k}: {v}")
        return

    if args.benchmark:
        print("Benchmarking full file read...")
        t0 = time.perf_counter()
        data = reader.to_dict(skip_inactive=True, compute_derived=True)
        t1 = time.perf_counter()
        n = len(data.get('Time', []))
        print(f"  Read {n:,} samples in {t1-t0:.3f}s ({n/(t1-t0):,.0f} samples/s)")

        if args.start and args.end:
            print(f"\nBenchmarking segment read ({args.start}ms - {args.end}ms)...")
            t0 = time.perf_counter()
            data = reader.to_dict(start_ms=args.start, end_ms=args.end)
            t1 = time.perf_counter()
            n = len(data.get('Time', []))
            print(f"  Read {n:,} samples in {t1-t0:.3f}s ({n/(t1-t0):,.0f} samples/s)")
        return

    # Read data
    print(f"Reading {args.file}...")
    t0 = time.perf_counter()

    if args.parquet or args.csv:
        df = reader.to_dataframe(start_ms=args.start, end_ms=args.end)
        t1 = time.perf_counter()
        print(f"  {len(df):,} rows × {len(df.columns)} columns in {t1-t0:.3f}s")

        if args.parquet:
            df.to_parquet(args.parquet, index=False)
            print(f"  Saved to {args.parquet}")
        if args.csv:
            df.to_csv(args.csv, index=False)
            print(f"  Saved to {args.csv}")
    else:
        data = reader.to_dict(start_ms=args.start, end_ms=args.end)
        t1 = time.perf_counter()
        n = len(data.get('Time', []))
        print(f"  {n:,} samples, {len(data)} columns in {t1-t0:.3f}s")
        if n > 0:
            print(f"  Time range: {data['Time'][0]:.0f} - {data['Time'][-1]:.0f} ms")
            print(f"  Columns: {', '.join(sorted(data.keys()))}")


if __name__ == '__main__':
    main()
