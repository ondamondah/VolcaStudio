"""Generate a small original WAV with alternating tones for the Windows preview."""
import math
import struct
import sys
import wave

rate = 44100
with wave.open(sys.argv[1], "wb") as output:
    output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
    for beat in range(8):
        frequency = (220, 330, 440, 550)[beat % 4]
        samples = [
            int(12000 * math.sin(math.tau * frequency * i / rate)
                * min(1, i / 220) * max(0, 1 - i / 8800))
            for i in range(rate // 2)
        ]
        output.writeframes(struct.pack(f"<{len(samples)}h", *samples))
