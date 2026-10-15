"""Daikin 280-bit (three-frame "DAIKIN") IR protocol, as used by codeset 1101.

Pure functions only (no Home Assistant imports) so tests can run standalone.

Every transmission carries the whole state:

    lead    : 5 zero bits, mark, gap (no header)
    frame 1 : 11 DA 27 00 C5 00 00 D7                    (constant)
    frame 2 : 11 DA 27 00 42 <clock lo> <clock hi> <sum> (clock is ignored by the unit)
    frame 3 : 11 DA 27 00 00
              [5]  mode<<4 | 0x08 | power
              [6]  temperature * 2  (dry: 0xC0, fan_only: 0x32)
              [8]  fan<<4
              [11..12] 06 60 (timers unused)
              [13] powerful
              [15] C1
              [18] sum

Field values are taken from decoding all 521 Broadlink captures in
ar_smart_ir's codes/climate/1101.json; tests/test_protocol.py checks this
module against them byte for byte.
"""

from __future__ import annotations

MODES = {"heat_cool": 0x0, "dry": 0x2, "cool": 0x3, "heat": 0x4, "fan_only": 0x6}
FANS = {
    "auto": 0xA,
    "night": 0xB,
    "low": 0x3,
    "lowMedium": 0x4,
    "medium": 0x5,
    "mediumHigh": 0x6,
    "high": 0x7,
    "powerful": 0xA,
}
MIN_TEMP = 18
MAX_TEMP = 30

# Codeset 1101 pins the setpoint while "powerful" is active.
_POWERFUL_TEMP = {"cool": 25, "heat": 30, "heat_cool": 30}
_DRY_TEMP_BYTE = 0xC0
_FAN_ONLY_TEMP = 25

FRAME1 = bytes.fromhex("11da2700c50000d7")
_FRAME2_BASE = bytes.fromhex("11da270042")
_FRAME3_BASE = bytes.fromhex("11da270000000000000000066000" "00c10000")

# Timings in microseconds, medians of the 1101 Broadlink captures.
BIT_MARK = 430
ZERO_SPACE = 430
ONE_SPACE = 1340
HEADER_MARK = 3470
HEADER_SPACE = 1770
LEAD_BITS = 5
LEAD_GAP = 25500
FRAME_GAP = 35000


def _with_checksum(body: bytes) -> bytes:
    return bytes(body) + bytes([sum(body) & 0xFF])


def build_frame2(clock_minutes: int = 0) -> bytes:
    """Frame 2 carries the remote's clock; the unit does not act on it."""
    clock = clock_minutes % 1440
    return _with_checksum(_FRAME2_BASE + bytes([clock & 0xFF, 0x30 | (clock >> 8)]))


def build_frame3(power: bool, mode: str, temperature: int, fan: str) -> bytes:
    """Build the 19-byte state frame.

    Power off is sent as fan_only/25 with the power bit cleared, matching the
    codeset's "off" capture.
    """
    if mode not in MODES:
        raise ValueError(f"Unsupported mode: {mode}")
    if fan not in FANS:
        raise ValueError(f"Unsupported fan mode: {fan}")

    frame = bytearray(_FRAME3_BASE)
    if not power:
        frame[5] = MODES["fan_only"] << 4 | 0x08
        frame[6] = _FAN_ONLY_TEMP * 2
        frame[8] = FANS["auto"] << 4
        return _with_checksum(frame)

    powerful = fan == "powerful"
    frame[5] = MODES[mode] << 4 | 0x09

    if mode == "dry":
        frame[6] = _DRY_TEMP_BYTE
    elif mode == "fan_only":
        frame[6] = _FAN_ONLY_TEMP * 2
    else:
        temp = _POWERFUL_TEMP[mode] if powerful else int(temperature)
        if not MIN_TEMP <= temp <= MAX_TEMP:
            raise ValueError(f"Temperature out of range: {temp}")
        frame[6] = temp * 2

    # Dry always runs the fan on auto.
    frame[8] = (FANS["auto"] if mode == "dry" else FANS[fan]) << 4
    frame[13] = 1 if powerful else 0
    return _with_checksum(frame)


def build_frames(power: bool, mode: str, temperature: int, fan: str) -> list[bytes]:
    return [FRAME1, build_frame2(), build_frame3(power, mode, temperature, fan)]


def _bits(data: bytes) -> list[int]:
    out: list[int] = []
    for byte in data:
        for bit in range(8):  # LSB first
            out += [BIT_MARK, ONE_SPACE if byte >> bit & 1 else ZERO_SPACE]
    return out


def frames_to_timings(frames: list[bytes]) -> list[int]:
    """Unsigned alternating mark/space durations, starting with a mark."""
    timings = [BIT_MARK, ZERO_SPACE] * LEAD_BITS + [BIT_MARK, LEAD_GAP]
    for index, frame in enumerate(frames):
        timings += [HEADER_MARK, HEADER_SPACE] + _bits(frame)
        timings += [BIT_MARK] if index == len(frames) - 1 else [BIT_MARK, FRAME_GAP]
    return timings
