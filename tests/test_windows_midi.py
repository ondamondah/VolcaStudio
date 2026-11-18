import struct
import unittest
from queue import SimpleQueue
from unittest.mock import MagicMock, patch

from app.device import volca_sample2 as volca


class WindowsMidiTest(unittest.TestCase):
    def test_identify_headers_and_audio_through_one_input(self):
        packets = SimpleQueue()
        output = MagicMock()
        audio = bytes(range(256)) * 600
        slot_header = struct.pack("<24sIHH", b"Test audio", len(audio) // 2, 65535, 16384)
        truncated = False

        def device_response(message):
            data = bytes(message.data)
            if data == bytes.fromhex("7e 7f 06 01"):
                reply = b"\x7e\x00\x06\x02" + volca._IDENTITY + bytes(4)
            elif data == volca._HEADER + b"\x1e\x00\x00":
                reply = volca._HEADER + b"\x4e\x00\x00" + volca._pack(slot_header)
            elif data == volca._HEADER + b"\x1f\x00\x00":
                reply = volca._HEADER + b"\x4f\x00\x00" + volca._pack(audio)
                if truncated:
                    reply = reply[:-10]
            else:
                self.fail(f"Unexpected MIDI request: {data.hex()}")
            wire = b"\xf0" + reply + b"\xf7"
            packets.put(wire[:4])
            for start in range(4, len(wire), 65536):
                packets.put(wire[start:start + 65536])

        output.send.side_effect = device_response
        with (patch.object(volca.sys, "platform", "win32"),
              patch.object(volca, "WinMMInput") as source,
              patch.object(volca, "_ports", return_value=("Volca", "Volca")),
              patch.object(volca.mido, "open_output") as open_output,
              patch.object(volca.time, "sleep")):
            source.return_value.__enter__.return_value = packets
            open_output.return_value.__enter__.return_value = output
            with volca._device_session() as (_, read, send):
                frames = len(audio) // 2
                self.assertEqual(read(0)[1], frames)
                self.assertEqual(volca._read_sample_pcm(0, frames, send), audio)
                truncated = True
                with self.assertRaisesRegex(volca.InvalidSlotReplyError, "Incomplete audio"):
                    volca._read_sample_pcm(0, frames, send)
            source.assert_called_once()


if __name__ == "__main__":
    unittest.main()
