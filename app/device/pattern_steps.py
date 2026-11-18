"""Decode trigger masks in the documented Volca Sample 2 pattern format."""

from app.device.volca_sample2 import PATTERN_SIZE

PART_COUNT = 10
STEP_COUNT = 16
_PART_START = 128
_PART_SIZE = 768


def read_steps(data):
    if (len(data) != PATTERN_SIZE or data[:6] != b"PTST\xb8\x33"
            or data[-4:] != b"PTED"):
        raise ValueError("This pattern does not use the supported Volca Sample 2 format.")
    active = int.from_bytes(data[8:10], "little")
    parts = []
    for part in range(PART_COUNT):
        start = _PART_START + part * _PART_SIZE
        parts.append((data[start], int.from_bytes(data[start + 2:start + 4], "little")))
    return active, parts


def write_active_steps(data, active):
    read_steps(data)
    if type(active) is not int or not 0 <= active < 1 << STEP_COUNT:
        raise ValueError("Invalid active step mask.")
    return data[:8] + active.to_bytes(2, "little") + data[10:]


def write_steps(data, masks):
    read_steps(data)
    if len(masks) != PART_COUNT or any(type(mask) is not int or not 0 <= mask < 1 << STEP_COUNT for mask in masks):
        raise ValueError("Invalid pattern step masks.")
    result = bytearray(data)
    for part, mask in enumerate(masks):
        start = _PART_START + part * _PART_SIZE + 2
        result[start:start + 2] = mask.to_bytes(2, "little")
    return bytes(result)


def write_part_samples(data, slots):
    _, parts = read_steps(data)
    if len(slots) != PART_COUNT:
        raise ValueError("Invalid pattern part count.")
    result = bytearray(data)
    for part, (slot, (original, _)) in enumerate(zip(slots, parts)):
        if type(slot) is not int or (slot != original and not 0 <= slot < 200):
            raise ValueError("Invalid sample slot.")
        result[_PART_START + part * _PART_SIZE] = slot
    return bytes(result)


def remap_sample_slots(data, replacements):
    """Apply original-to-destination slot mappings to a copy of the pattern."""
    _, parts = read_steps(data)
    if any(type(old) is not int or not 0 <= old < 200
           or type(new) is not int or not 0 <= new < 200
           for old, new in replacements.items()):
        raise ValueError("Invalid sample slot mapping.")
    return write_part_samples(data, [replacements.get(slot, slot) for slot, _ in parts])


def read_part_sound(data):
    read_steps(data)
    return [
        tuple(data[start + 10:start + 13])
        for start in range(_PART_START, _PART_START + PART_COUNT * _PART_SIZE, _PART_SIZE)
    ]


def write_part_sound(data, settings):
    original = read_part_sound(data)
    if len(settings) != PART_COUNT:
        raise ValueError("Invalid pattern part count.")
    result = bytearray(data)
    for part, (values, previous) in enumerate(zip(settings, original)):
        if len(values) != 3:
            raise ValueError("Invalid part sound parameters.")
        for index, (value, before) in enumerate(zip(values, previous)):
            if type(value) is not int or not 0 <= value <= 255:
                raise ValueError("Invalid part sound parameter.")
            valid = value <= 127 if index < 2 else 40 <= value <= 88 or value >= 128
            if value != before and not valid:
                raise ValueError("Invalid part sound parameter.")
        start = _PART_START + part * _PART_SIZE + 10
        result[start:start + 3] = bytes(values)
    return bytes(result)


_MOTION_OFFSETS = (64, 128, 192)


def read_step_motion(data, part, step):
    start = _PART_START + part * _PART_SIZE + step * 4
    return tuple(tuple(data[start + offset:start + offset + 4]) for offset in _MOTION_OFFSETS)


def write_step_motion(data, changes):
    """Set constant values across the four motion points of each edited step."""
    read_steps(data)
    result = bytearray(data)
    for (part, step, parameter), value in changes.items():
        if not (0 <= part < PART_COUNT and 0 <= step < STEP_COUNT
                and 0 <= parameter < len(_MOTION_OFFSETS) and 0 <= value <= 127):
            raise ValueError("Invalid step motion value.")
        start = _PART_START + part * _PART_SIZE
        offset = start + _MOTION_OFFSETS[parameter] + step * 4
        result[offset:offset + 4] = bytes([128 + value]) * 4
        result[start + 9] |= 1
    return bytes(result)
