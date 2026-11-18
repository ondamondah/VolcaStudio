"""Read the MIDI bytes that CoreMIDI delivers before SysEx message assembly."""

import ctypes as ct
import platform
import struct
from contextlib import contextmanager
from queue import SimpleQueue


_READ = ct.CFUNCTYPE(None, ct.c_void_p, ct.c_void_p, ct.c_void_p)
_ARM_ALIGNMENT = platform.machine() in {"arm64", "aarch64"}


@contextmanager
def volca_packets():
    midi = ct.CDLL("/System/Library/Frameworks/CoreMIDI.framework/CoreMIDI")
    cf = ct.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFStringCreateWithCString.argtypes = (ct.c_void_p, ct.c_char_p, ct.c_uint32)
    cf.CFStringCreateWithCString.restype = ct.c_void_p
    cf.CFStringGetCString.argtypes = (ct.c_void_p, ct.c_char_p, ct.c_long, ct.c_uint32)
    cf.CFStringGetCString.restype = ct.c_bool
    cf.CFRelease.argtypes = (ct.c_void_p,)
    midi.MIDIGetNumberOfSources.restype = ct.c_ulong
    midi.MIDIGetSource.argtypes = (ct.c_ulong,)
    midi.MIDIGetSource.restype = ct.c_uint32
    midi.MIDIObjectGetStringProperty.argtypes = (ct.c_uint32, ct.c_void_p, ct.POINTER(ct.c_void_p))
    midi.MIDIClientCreate.argtypes = (ct.c_void_p, ct.c_void_p, ct.c_void_p, ct.POINTER(ct.c_uint32))
    midi.MIDIInputPortCreate.argtypes = (
        ct.c_uint32, ct.c_void_p, _READ, ct.c_void_p, ct.POINTER(ct.c_uint32),
    )
    midi.MIDIPortConnectSource.argtypes = (ct.c_uint32, ct.c_uint32, ct.c_void_p)
    midi.MIDIPortDispose.argtypes = (ct.c_uint32,)
    midi.MIDIClientDispose.argtypes = (ct.c_uint32,)

    utf8 = 0x08000100
    display_name = ct.c_void_p.in_dll(midi, "kMIDIPropertyDisplayName")
    sources = []
    for index in range(midi.MIDIGetNumberOfSources()):
        endpoint = midi.MIDIGetSource(index)
        name = ct.c_void_p()
        if midi.MIDIObjectGetStringProperty(endpoint, display_name, ct.byref(name)) != 0:
            continue
        try:
            buffer = ct.create_string_buffer(256)
            if cf.CFStringGetCString(name, buffer, len(buffer), utf8) and b"volca sample" in buffer.value.lower():
                sources.append(endpoint)
        finally:
            cf.CFRelease(name)
    if len(sources) != 1:
        raise RuntimeError("Exactly one Volca CoreMIDI input is required.")

    packets = SimpleQueue()

    @_READ
    def receive(packet_list, _context, _source):
        # The legacy MIDIPacketList header is 4 bytes, then timestamp and length
        # occupy 10 bytes per packet. Only ARM pads subsequent packets to 4 bytes.
        count = ct.c_uint32.from_address(packet_list).value
        cursor = packet_list + 4
        received = bytearray()
        for _ in range(count):
            length, = struct.unpack("=H", ct.string_at(cursor + 8, 2))
            received.extend(ct.string_at(cursor + 10, length))
            cursor += 10 + length
            if _ARM_ALIGNMENT:
                cursor = (cursor + 3) & ~3
        if received:
            packets.put(bytes(received))

    client = ct.c_uint32()
    port = ct.c_uint32()
    label = cf.CFStringCreateWithCString(None, b"VolcaStudio Audio Read", utf8)
    try:
        if midi.MIDIClientCreate(label, None, None, ct.byref(client)) != 0:
            raise RuntimeError("Could not create a CoreMIDI client.")
        if midi.MIDIInputPortCreate(client, label, receive, None, ct.byref(port)) != 0:
            raise RuntimeError("Could not open the CoreMIDI input.")
        if midi.MIDIPortConnectSource(port, sources[0], None) != 0:
            raise RuntimeError("Could not connect to the Volca CoreMIDI input.")
        yield packets
    finally:
        if port.value:
            midi.MIDIPortDispose(port)
        if client.value:
            midi.MIDIClientDispose(client)
        cf.CFRelease(label)
