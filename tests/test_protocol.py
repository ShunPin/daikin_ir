"""Golden test: protocol.py must reproduce every capture in ar_smart_ir's 1101.json."""

import base64
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "daikin_ir" / "custom_components" / "daikin_ir"))

import protocol  # noqa: E402
from tuya_codec import decode_tuya, encode_tuya  # noqa: E402

CODESET = ROOT / "ar_smart_ir" / "custom_components" / "ar_smart_ir" / "codes" / "climate" / "1101.json"

# The codeset itself captured heat/lowMedium/28 with a 27 °C setpoint.
KNOWN_CODESET_ERRORS = {("heat", "lowMedium", "28")}

# 50-byte chunks per zosung transfer; the old Broadlink-derived codes needed 11-13.
MAX_ZOSUNG_MESSAGE = 300


def _broadlink_pulses(code: str) -> list[int]:
    raw = base64.b64decode(code)
    length = raw[2] | raw[3] << 8
    data = raw[4 : 4 + length]
    pulses, i = [], 0
    while i < len(data):
        value = data[i]
        i += 1
        if value == 0:
            value = data[i] << 8 | data[i + 1]
            i += 2
        pulses.append(round(value * 8192 / 269))
    return pulses


def _decode_frames(pulses: list[int]) -> list[bytes]:
    frames, bits, i = [], [], 0
    while i + 1 < len(pulses):
        mark, space = pulses[i], pulses[i + 1]
        i += 2
        if mark > 2500:  # header starts a new frame
            if bits:
                frames.append(bits)
            bits = []
            continue
        bits.append(1 if space > 900 else 0)
        if space > 5000:
            frames.append(bits)
            bits = []
    if bits:
        frames.append(bits)
    return [bytes(sum(b << k for k, b in enumerate(f[n : n + 8])) for n in range(0, len(f) - len(f) % 8, 8)) for f in frames]


def _captures():
    commands = json.loads(CODESET.read_text())["commands"]
    yield (False, "cool", 26, "auto"), ("off", "-", "-"), commands["off"]
    for mode, fans in commands.items():
        if mode == "off":
            continue
        for fan, temps in fans.items():
            for temp, code in temps.items():
                yield (True, mode, int(temp), fan), (mode, fan, temp), code


def test_frames_match_codeset():
    checked, mismatches = 0, []
    for args, key, code in _captures():
        frames = _decode_frames(_broadlink_pulses(code))
        f1 = [f for f in frames if len(f) == 8 and f[4] == 0xC5]
        f3 = [f for f in frames if len(f) == 19]
        assert f1 == [protocol.FRAME1], key
        expected = protocol.build_frame3(*args)
        if key in KNOWN_CODESET_ERRORS:
            assert f3[0] != expected, f"{key} no longer differs; drop it from KNOWN_CODESET_ERRORS"
            continue
        if f3[0] != expected:
            mismatches.append((key, f3[0].hex(" "), expected.hex(" ")))
        checked += 1
    assert not mismatches, mismatches[:5]
    assert checked == 520


def test_timings_roundtrip_and_size():
    frames = protocol.build_frames(True, "cool", 26, "auto")
    timings = protocol.frames_to_timings(frames)
    assert _decode_frames(timings)[1:] == frames
    code = encode_tuya(timings)
    assert decode_tuya(code) == timings
    message = json.dumps({"key_num": 1, "delay": 300, "key1": {"num": 1, "freq": 38000, "type": 1, "key_code": code}})
    assert len(message) <= MAX_ZOSUNG_MESSAGE, len(message)


def test_frame2_checksum():
    for minutes in (0, 59, 1439, 1500):
        frame = protocol.build_frame2(minutes)
        assert frame[-1] == sum(frame[:-1]) & 0xFF


def test_rejects_unknown_values():
    for args in ((True, "auto", 26, "auto"), (True, "cool", 26, "turbo"), (True, "cool", 31, "auto")):
        try:
            protocol.build_frame3(*args)
        except ValueError:
            continue
        raise AssertionError(f"accepted {args}")
