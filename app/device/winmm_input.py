"""Read raw SysEx chunks from a WinMM MIDI input."""
import ctypes as ct
from ctypes import wintypes as wt
from queue import SimpleQueue


class MidiInCaps(ct.Structure):
    _fields_ = [("manufacturer", wt.WORD), ("product", wt.WORD),
                ("version", wt.UINT), ("name", wt.WCHAR * 32),
                ("support", wt.DWORD)]


class MidiHeader(ct.Structure):
    _fields_ = [("data", ct.c_void_p), ("capacity", wt.DWORD),
                ("recorded", wt.DWORD), ("user", ct.c_size_t),
                ("flags", wt.DWORD), ("next", ct.c_void_p),
                ("reserved", ct.c_size_t), ("offset", wt.DWORD),
                ("driver_reserved", ct.c_size_t * 8)]


class WinMMInput:
    def __init__(self):
        self.driver = ct.WinDLL("winmm")
        self.handle = ct.c_void_p()
        self.completed = SimpleQueue()
        self.buffers = []
        callback = ct.WINFUNCTYPE(None, ct.c_void_p, wt.UINT,
                                  ct.c_size_t, ct.c_size_t, ct.c_size_t)
        self.callback = callback(self._receive)
        signatures = {
            "midiInGetNumDevs": (),
            "midiInGetDevCapsW": (ct.c_size_t, ct.POINTER(MidiInCaps), wt.UINT),
            "midiInOpen": (ct.POINTER(ct.c_void_p), wt.UINT, callback, ct.c_size_t, wt.DWORD),
        }
        for name in ("Start", "Reset", "Close"):
            signatures["midiIn" + name] = (ct.c_void_p,)
        for name in ("PrepareHeader", "AddBuffer", "UnprepareHeader"):
            signatures["midiIn" + name] = (ct.c_void_p, ct.POINTER(MidiHeader), wt.UINT)
        for name, args in signatures.items():
            function = getattr(self.driver, name)
            function.argtypes = args
            function.restype = wt.UINT

    def _call(self, name, *args):
        code = getattr(self.driver, name)(*args)
        if code:
            raise RuntimeError(f"{name} failed (WinMM error {code}).")

    def _header_call(self, name, header):
        self._call(name, self.handle, ct.byref(header), ct.sizeof(header))

    def _receive(self, _handle, message, _instance, header, _timestamp):
        # WinMM forbids calling its MIDI functions from this callback.
        if message in (0x3C4, 0x3C6) and header:  # MIM_LONGDATA, MIM_LONGERROR
            self.completed.put((header, message == 0x3C6))

    def __enter__(self):
        candidates = []
        for index in range(self.driver.midiInGetNumDevs()):
            caps = MidiInCaps()
            if not self.driver.midiInGetDevCapsW(index, ct.byref(caps), ct.sizeof(caps)):
                if "volca sample" in caps.name.casefold():
                    candidates.append(index)
        if not candidates:
            raise RuntimeError("No Volca Sample 2 MIDI input found. Connect it via USB and turn it on.")
        if len(candidates) != 1:
            raise RuntimeError("Exactly one Volca WinMM input is required.")
        self._call("midiInOpen", ct.byref(self.handle), candidates[0], self.callback, 0, 0x30000)
        try:
            for _ in range(8):
                data = ct.create_string_buffer(65536)
                header = MidiHeader(ct.addressof(data), len(data))
                self._header_call("midiInPrepareHeader", header)
                self.buffers.append((data, header))
                self._header_call("midiInAddBuffer", header)
            self._call("midiInStart", self.handle)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def get(self, timeout=None):
        address, failed = self.completed.get(timeout=timeout)
        header = MidiHeader.from_address(address)
        chunk = ct.string_at(header.data, header.recorded)
        self._header_call("midiInAddBuffer", header)
        return b"\xf0\x80" if failed else chunk

    def get_nowait(self):
        return self.get(timeout=0)

    def __exit__(self, *_):
        self._call("midiInReset", self.handle)
        for _, header in self.buffers:
            self._header_call("midiInUnprepareHeader", header)
        self._call("midiInClose", self.handle)
        self.buffers.clear()
