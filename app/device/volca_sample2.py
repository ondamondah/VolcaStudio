"""Single-slot USB transfer. Wire format documented by the Elektroid connector."""
import os
import struct
import subprocess
import sys
import tempfile
import time
import wave
from contextlib import contextmanager, nullcontext
from pathlib import Path
from queue import Empty

import mido
import rtmidi

from app.audio.converter import convert_pcm_wav
from app.device.coremidi_input import volca_packets
from app.device.winmm_input import WinMMInput

_HEADER = bytes.fromhex("42 30 00 01 2d")
_IDENTITY = bytes.fromhex("42 2d 01 08 00")
_RATE = 31250
# The hardware stores 2047 frames when a new sample is exactly 2048 frames long.
_TRUNCATED_UPLOAD_FRAMES = 2048
# Keep the packed SysEx below the 1 MiB interoperability ceiling.
MAX_TRANSFER_SECONDS = 14
PATTERN_SIZE = 7936


def _pack(raw):
    packed = bytearray()
    for offset in range(0, len(raw), 7):
        block = raw[offset:offset + 7]
        packed.append(sum((value >> 7) << index for index, value in enumerate(block)))
        packed.extend(value & 127 for value in block)
    return packed


def _unpack(packed):
    raw = bytearray()
    for offset in range(0, len(packed), 8):
        flags = packed[offset]
        raw.extend(value | ((flags >> index & 1) << 7)
                   for index, value in enumerate(packed[offset + 1:offset + 8]))
    return raw


def _prepare(source):
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "sample.wav"
        try:
            convert_pcm_wav(Path(source).resolve(), target, rate=_RATE, channels=1)
        except subprocess.CalledProcessError as exc:
            raise ValueError("Could not convert this audio: " + exc.stderr.decode(errors="replace").strip()) from exc
        with wave.open(str(target), "rb") as wav:
            frames = wav.getnframes()
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, _RATE):
                raise ValueError("Audio conversion produced an unexpected format.")
            if not 0 < frames <= _RATE * MAX_TRANSFER_SECONDS:
                raise ValueError(f"Choose a non-empty sample no longer than {MAX_TRANSFER_SECONDS} seconds.")
            pcm = wav.readframes(frames)
        if len(pcm) != frames * 2:
            raise ValueError("Converted audio is incomplete.")
        if frames == _TRUNCATED_UPLOAD_FRAMES:
            return frames + 1, pcm + b"\0\0"
        return frames, pcm


def _ports():
    inputs = [name for name in mido.get_input_names() if "volca sample" in name.casefold()]
    outputs = [name for name in mido.get_output_names() if "volca sample" in name.casefold()]
    if len(inputs) != 1 or len(outputs) != 1:
        raise RuntimeError("Connect one Volca Sample 2 via USB. Exactly one Volca input and output are required.")
    return inputs[0], outputs[0]


def _identify_device(exchange):
    def matches(reply):
        return (len(reply) == 13 and reply[0] == 0x7e
                and reply[2:9] == b"\x06\x02" + _IDENTITY)

    for attempt in range(3):
        try:
            return exchange(bytes.fromhex("7e 7f 06 01"), matches, "Identify device", timeout=3)
        except TimeoutError:
            if attempt == 2:
                raise
            time.sleep(0.35)


@contextmanager
def _midi_input(input_name):
    if sys.platform == "win32":
        with WinMMInput() as packets:
            yield packets, True
        return
    midi_in = rtmidi.MidiIn()
    midi_in.ignore_types(False, True, True)
    midi_in.open_port(midi_in.get_ports().index(input_name))
    try:
        yield midi_in, False
    finally:
        midi_in.close_port()


class _MidiSender:
    def __init__(self, output, packets):
        self.output = output
        self.packets = packets

    def __call__(self, data):
        message = data if isinstance(data, mido.Message) else mido.Message("sysex", data=data)
        self.output.send(message)


def _read_windows_reply(packets, deadline):
    message = bytearray()
    while time.monotonic() < deadline:
        try:
            chunk = packets.get(timeout=max(0, deadline - time.monotonic()))
        except Empty:
            break
        for byte in chunk:
            if byte == 0xf0:
                message[:] = b"\xf0"
            elif message and byte == 0xf7:
                message.append(byte)
                return message
            elif message and byte < 0xf8:
                message.append(byte)
    return None


@contextmanager
def _device_session():
    input_name, output_name = _ports()
    with _midi_input(input_name) as (midi_in, raw), mido.open_output(output_name) as output:
        send = _MidiSender(output, midi_in if raw else None)

        def exchange(data, matches, stage, *, timeout=8):
            if raw:
                while True:
                    try:
                        midi_in.get_nowait()
                    except Empty:
                        break
            else:
                while midi_in.get_message() is not None:
                    pass
            send(data)
            deadline = time.monotonic() + timeout
            last_reply = None
            while time.monotonic() < deadline:
                if raw:
                    message = _read_windows_reply(midi_in, deadline)
                    if message is None:
                        break
                else:
                    event = midi_in.get_message()
                    if event is None:
                        time.sleep(0.002)
                        continue
                    message, _ = event
                if len(message) < 2 or message[0] != 0xf0 or message[-1] != 0xf7:
                    continue
                reply = bytes(value for value in message[1:-1] if value < 0xf8)
                if any(value >= 0x80 for value in reply):
                    continue
                if matches(reply):
                    return reply
                last_reply = reply
            detail = f" Unexpected reply: {last_reply[:10].hex(' ')}." if last_reply else ""
            raise TimeoutError(f"{stage}: no matching reply from Volca.{detail}")

        _identify_device(exchange)

        def read_header(number):
            address = bytes((number & 127, number >> 7))
            prefix = _HEADER + b"\x4e" + address
            request = _HEADER + b"\x1e" + address
            for attempt in range(2):
                try:
                    reply = exchange(request, lambda data: data.startswith(prefix),
                                     f"Read slot {number:03d}")
                except TimeoutError:
                    if attempt == 1:
                        raise
                else:
                    if len(reply) == 45:
                        break
                    if attempt == 1:
                        raise InvalidSlotReplyError(
                            f"Read slot {number:03d}: expected 45 reply bytes, got {len(reply)}."
                        )
                time.sleep(0.05)
            return struct.unpack("<24sIHH", _unpack(reply[8:]))

        yield exchange, read_header, send


def list_slots():
    with _device_session() as (_, read_header, _):
        return _read_slot_list(read_header, range(200))


def slot_overview():
    with _device_session() as (exchange, read_header, _):
        slots = _read_slot_list(read_header, range(200))
        try:
            memory = _sample_memory(exchange)
        except (TimeoutError, ValueError):
            memory = None
        return slots, memory


def _read_slot_list(read_header, numbers):
    slots = []
    for number in numbers:
        name, frames, _, _ = read_header(number)
        name = name.split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
        slots.append((number, name, frames / _RATE if frames else None))
        time.sleep(0.005)
    return slots


def transfer_state(numbers):
    numbers = tuple(numbers)
    if any(type(slot) is not int or not 0 <= slot < 200 for slot in numbers):
        raise ValueError("Choose device slots between 000 and 199.")
    with _device_session() as (exchange, read_header, _):
        return _read_slot_list(read_header, numbers), _free_sample_memory(exchange)


def estimated_sample_memory(duration):
    frames = round(duration * _RATE)
    if frames == _TRUNCATED_UPLOAD_FRAMES:
        frames += 1
    return sample_memory_cost(frames * 2)


def read_pattern(slot):
    """Read a stored pattern (01–16) as a name and decoded binary data."""
    if not 1 <= slot <= 16:
        raise ValueError("Choose a pattern slot between 01 and 16.")
    with _device_session() as (exchange, _, _):
        return _read_pattern(exchange, slot)


def read_patterns():
    with _device_session() as (exchange, _, _):
        patterns = []
        for slot in range(1, 17):
            name, data = _read_pattern(exchange, slot)
            patterns.append((slot, name, data))
            time.sleep(0.05)
        return patterns


def _read_pattern(exchange, slot):
    address = bytes((slot - 1,))
    prefix = _HEADER + b"\x4d" + address
    return _read_pattern_dump(exchange, _HEADER + b"\x1d" + address, prefix, f"Read pattern {slot:02d}")


def read_current_pattern():
    with _device_session() as (exchange, _, _):
        return _read_current_pattern(exchange)


def _read_current_pattern(exchange):
    return _read_pattern_dump(exchange, _HEADER + b"\x11", _HEADER + b"\x41", "Read current pattern")


def _read_pattern_dump(exchange, request, prefix, stage):
    size = PATTERN_SIZE
    expected = len(prefix) + size + (size + 6) // 7
    for attempt in range(3):
        try:
            reply = exchange(
                request,
                lambda data: data.startswith(prefix) and len(data) == expected,
                stage,
            )
        except TimeoutError:
            if attempt == 2:
                raise
        else:
            packed = reply[len(prefix):]
            data = bytes(_unpack(packed))
            if _pack(data) == packed:
                break
            if attempt == 2:
                raise ValueError(f"{stage}: invalid pattern data.")
        time.sleep(0.25 * (attempt + 1))
    name = data[16:48].split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
    return name, data


def _write_current_pattern(exchange, pattern):
    response = exchange(
        _HEADER + b"\x41" + _pack(pattern),
        lambda data: len(data) == 6 and data.startswith(_HEADER) and data[5] in (0x23, 0x24, 0x26),
        "Load preview into the current pattern",
    )
    if response[5] != 0x23 or _read_current_pattern(exchange)[1] != pattern:
        raise RuntimeError("Could not verify the current pattern on Volca.")


def preview_pattern_on_device(pattern, bpm, stop, save_previous):
    if len(pattern) != PATTERN_SIZE or not 30 <= bpm <= 300:
        raise ValueError("Invalid pattern or tempo.")
    with _device_session() as (exchange, _, send):
        name, original = _read_current_pattern(exchange)
        changed = original != pattern
        if changed:
            save_previous(name, original)
        if stop.is_set():
            return
        started = False
        try:
            if changed:
                _write_current_pattern(exchange, pattern)
            if stop.is_set():
                return
            send(mido.Message("start"))
            started = True
            clock = mido.Message("clock")
            interval = 60 / (24 * bpm)
            next_tick = time.monotonic()
            while not stop.is_set():
                send(clock)
                next_tick += interval
                stop.wait(max(0, next_tick - time.monotonic()))
        finally:
            try:
                if started:
                    send(mido.Message("stop"))
            finally:
                if changed:
                    current = _read_current_pattern(exchange)[1]
                    if current == pattern:
                        _write_current_pattern(exchange, original)
                    elif current != original:
                        raise RuntimeError("The current pattern changed during preview. The recovery copy was kept.")


def restore_pattern(slot, replacement, expected, save_previous):
    """Replace one stored pattern after saving and checking its current data."""
    if not 1 <= slot <= 16 or len(replacement) != PATTERN_SIZE or len(expected) != PATTERN_SIZE:
        raise ValueError("Invalid pattern slot or file size.")
    with _device_session() as (exchange, _, _):
        name, current = _read_pattern(exchange, slot)
        if current != expected:
            raise ValueError(f"Slot {slot:02d} changed since comparison. Compare with Volca again.")
        if current == replacement:
            return
        save_previous(slot, name, current)
        _write_pattern(exchange, slot, replacement)


def _write_pattern(exchange, slot, replacement):
    time.sleep(0.05)
    request = _HEADER + bytes((0x4d, slot - 1)) + _pack(replacement)
    reply = exchange(
        request,
        lambda data: len(data) == 6 and data.startswith(_HEADER) and data[5] in (0x23, 0x24, 0x25, 0x26),
        f"Write pattern {slot:02d}",
    )
    if reply[5] != 0x23:
        raise RuntimeError(f"Volca rejected the write for pattern {slot:02d} (code {reply[5]:02x}).")
    time.sleep(0.05)
    if _read_pattern(exchange, slot)[1] != replacement:
        raise RuntimeError(f"Could not verify pattern {slot:02d} after writing.")


def _clear_sample(exchange, read_header, slot):
    address = bytes((slot & 127, slot >> 7))
    for opcode, payload in ((0x4e, _pack(bytes(32))), (0x4f, b"")):
        for attempt in range(3):
            reply = exchange(
                _HEADER + bytes((opcode,)) + address + payload,
                lambda data: len(data) >= 6 and data.startswith(_HEADER)
                and data[5] in (0x23, 0x24, 0x25, 0x26),
                f"Clear sample {slot:03d}",
            )
            if reply[5] != 0x24 or attempt == 2:
                break
            time.sleep(0.15)
        if reply[5] != 0x23:
            raise RuntimeError(f"Volca rejected clearing sample {slot:03d} (code {reply[5]:02x}).")
        time.sleep(0.02)
    if read_header(slot)[1]:
        raise RuntimeError(f"Could not verify empty sample slot {slot:03d}.")


class EmptySampleSlotError(ValueError):
    pass


class InvalidSlotReplyError(RuntimeError):
    pass


def _read_audio(packets, send, address, expected):
    prefix = _HEADER + b"\x4f" + address
    busy = _HEADER + b"\x24"
    while True:
        try:
            packets.get_nowait()
        except Empty:
            break
    send(_HEADER + b"\x1f" + address)
    deadline = time.monotonic() + 8
    quiet_until = deadline
    realtime = bytes(range(0xf8, 0x100))
    reply = None
    ended = False
    while time.monotonic() < quiet_until:
        try:
            chunk = packets.get(timeout=max(0, quiet_until - time.monotonic()))
        except Empty:
            break
        chunk = chunk.translate(None, realtime)
        if not chunk:
            continue
        if b"\xf0" in chunk:
            reply = bytearray()
            chunk = chunk.rsplit(b"\xf0", 1)[1]
            ended = False
        if reply is None or not chunk:
            continue
        ended = chunk.endswith(b"\xf7")
        data = chunk.replace(b"\xf7", b"")
        if data and max(data) >= 0x80:
            reply = None
            continue
        reply.extend(data)
        if ended and (reply == busy or (reply.startswith(prefix) and len(reply) >= expected)):
            return reply
        # CoreMIDI can deliver F7 before later packets of the same audio reply.
        # Give a short reply time to continue, ignoring standalone MIDI clock.
        quiet_until = min(deadline, time.monotonic() + 0.4) if ended and reply.startswith(prefix) else deadline
    if ended and reply is not None and reply.startswith(prefix):
        return reply
    raise TimeoutError("No matching audio reply from Volca.")


def _read_sample_pcm(slot, frames, send):
    if sys.platform not in ("darwin", "win32"):
        raise RuntimeError("USB sample audio readback is not available on this platform yet.")
    address = bytes((slot & 127, slot >> 7))
    if frames > 8 * 1024 * 1024 // 2:
        raise ValueError("Volca reported an invalid sample length.")
    size = frames * 2
    expected = 8 + size + (size + 6) // 7
    busy = _HEADER + b"\x24"
    raw_packets = getattr(send, "packets", None)
    with (nullcontext(raw_packets) if raw_packets is not None else volca_packets()) as packets:
        time.sleep(0.05)
        for attempt in range(3):
            try:
                reply = _read_audio(packets, send, address, expected)
            except TimeoutError as exc:
                if attempt == 2:
                    raise InvalidSlotReplyError(f"Read audio from slot {slot:03d}: {exc}") from exc
            else:
                if len(reply) == expected:
                    break
            if attempt < 2:
                time.sleep(0.3 * (attempt + 1))
    if reply == busy:
        raise InvalidSlotReplyError(f"Volca remained busy while reading slot {slot:03d} after three attempts.")
    if len(reply) != expected:
        raise InvalidSlotReplyError(
            f"Incomplete audio for slot {slot:03d}: received {len(reply)} of {expected} bytes. "
            "Stop playback on Volca and try again."
        )
    return _unpack(reply[8:])


def save_slot_wav(slot, destination):
    if not 0 <= slot < 200:
        raise ValueError("Choose a device slot between 000 and 199.")
    with _device_session() as (_, read_header, send):
        _, frames, _, _ = read_header(slot)
        if not frames:
            raise EmptySampleSlotError(f"Device slot {slot:03d} is empty.")
        pcm = _read_sample_pcm(slot, frames, send)

    destination = Path(destination)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as file:
            temporary = Path(file.name)
            with wave.open(file, "wb") as wav:
                wav.setparams((1, 2, _RATE, 0, "NONE", "not compressed"))
                wav.writeframes(pcm)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _free_sample_memory(exchange):
    used, total = _sample_memory(exchange)
    return total - used


def _sample_memory(exchange):
    prefix = _HEADER + b"\x4b"
    reply = exchange(_HEADER + b"\x1b", lambda data: len(data) == 10 and data.startswith(prefix),
                     "Read sample memory")
    used = reply[6] | (reply[7] << 7)
    total = reply[8] | (reply[9] << 7)
    if not 0 <= used <= total <= 2048 or total == 0:
        raise ValueError("Volca reported an invalid sample memory size.")
    return used * 4096, total * 4096


def sample_memory_cost(pcm_bytes):
    return ((pcm_bytes + 4095) // 4096) * 4096


def _write_sample(slot, header, pcm, exchange, read_header, send):
    if sys.platform not in ("darwin", "win32"):
        raise RuntimeError("USB sample audio readback is not available on this platform yet.")
    _, frames, _, _ = header
    def write(opcode, payload):
        reply = exchange(_HEADER + bytes((opcode,)) + address + _pack(payload),
                         lambda data: len(data) >= 6 and data.startswith(_HEADER)
                         and data[5] in (0x23, 0x24, 0x25, 0x26),
                         "Write sample header" if opcode == 0x4e else "Write audio")
        errors = {0x24: "Volca is busy.", 0x25: "Volca has insufficient memory.",
                  0x26: "Volca rejected the sample data."}
        if reply[5] != 0x23:
            raise RuntimeError(errors[reply[5]])
        time.sleep(0.005)

    address = bytes((slot & 127, slot >> 7))
    if read_header(slot)[1] != 0:
        raise ValueError(f"Device slot {slot:03d} already contains a sample.")
    free = _free_sample_memory(exchange)
    required = sample_memory_cost(len(pcm))
    if required > free:
        raise ValueError(f"Volca has {free / 1024:.0f} KiB free; this sample needs {required / 1024:.0f} KiB.")
    try:
        write(0x4e, struct.pack("<24sIHH", *header))
        write(0x4f, pcm)
        if read_header(slot) != header:
            raise RuntimeError("The slot header differs from the source.")
        if _read_sample_pcm(slot, frames, send) != pcm:
            raise RuntimeError("The audio read back from Volca differs from the source.")
    except Exception as exc:
        raise RuntimeError(f"Transfer could not be verified. Check slot {slot:03d} on Volca. {exc}") from exc


def send_sample(source, name, slot):
    if slot is not None and not 0 <= slot < 200:
        raise ValueError("Choose a device slot between 000 and 199.")
    frames, pcm = _prepare(source)
    device_name = name.encode("ascii", "replace")[:24] or b"Sample"
    header = (device_name.ljust(24, b"\0"), frames, 0xffff, 0x4000)
    with _device_session() as (exchange, read_header, send):
        if slot is None:
            slot = next((number for number in range(199, -1, -1)
                         if read_header(number)[1] == 0), None)
            if slot is None:
                raise ValueError("All device slots are occupied.")
        _write_sample(slot, header, pcm, exchange, read_header, send)
    return slot
