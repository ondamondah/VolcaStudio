import wave
import numpy as np



def fade_gain(total, fades, first, last):
    fade_in, fade_out, in_curve, out_curve = fades
    gain = np.ones(last - first)
    for length, curve, reverse in ((fade_in, in_curve, False), (fade_out, out_curve, True)):
        count = min(total, round(length * total))
        offset = total - count if reverse else 0
        start, end = max(first, offset), min(last, offset + count)
        if start < end:
            positions = np.arange(start - offset, end - offset)
            if reverse:
                positions = count - 1 - positions
            gain[start - first:end - first] *= (positions / max(1, count - 1)) ** curve
    return gain


def apply_fades(source, destination, fade_in, fade_out, in_curve=1.0, out_curve=1.0):
    if not (0 <= fade_in <= 1 and 0 <= fade_out <= 1 and fade_in + fade_out <= 1.000001):
        raise ValueError('Invalid fade lengths.')
    if not (0.15 <= in_curve <= 6 and 0.15 <= out_curve <= 6):
        raise ValueError('Invalid fade curve.')
    with wave.open(str(source), 'rb') as f:
        params = f.getparams()
        if params.sampwidth != 2 or params.comptype != 'NONE':
            raise ValueError('Fades require 16-bit PCM WAV.')
        data = np.frombuffer(f.readframes(params.nframes), dtype='<i2').reshape(-1, params.nchannels)
    gain = fade_gain(len(data), (fade_in, fade_out, in_curve, out_curve), 0, len(data))
    result = np.rint(data * gain[:, None]).clip(-32768, 32767).astype('<i2')
    with wave.open(str(destination), 'wb') as f:
        f.setparams(params); f.writeframes(result.tobytes())
