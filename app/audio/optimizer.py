"""Conservative edge trimming; retained PCM samples are copied unchanged."""
import wave
from pathlib import Path
import numpy as np


def optimize_silence(source, destination):
    if Path(source).resolve() == Path(destination).resolve():
        raise ValueError('The original must not be overwritten.')
    with wave.open(str(source), 'rb') as audio:
        if audio.getsampwidth() != 2 or audio.getcomptype() != 'NONE':
            raise ValueError('Optimization requires a 16-bit PCM WAV.')
        params = audio.getparams()
        raw = audio.readframes(audio.getnframes())
    frames = np.frombuffer(raw, dtype='<i2').reshape(-1, params.nchannels)
    count = len(frames)
    if not count:
        raise ValueError('The audio file is empty.')
    # Use every channel; a quiet channel cannot hide activity in another.
    envelope = np.max(np.abs(frames.astype(np.int32)), axis=1)
    peak = int(envelope.max())
    threshold = min(32768 * 10 ** (-72 / 20), peak * 0.001)
    active = np.flatnonzero(envelope > threshold)
    first, last = 0, count
    if active.size:
        first = max(0, int(active[0]) - int(params.framerate * 0.010))
        last = min(count, int(active[-1]) + 1 + int(params.framerate * 0.050))
        # Avoid negligible edits. Entirely silent files remain unchanged.
        if count - (last - first) < params.framerate * 0.020:
            first, last = 0, count
    with wave.open(str(destination), 'wb') as output:
        output.setparams(params)
        stride = params.nchannels * 2
        output.writeframes(raw[first * stride:last * stride])
    return {'before': count / params.framerate, 'after': (last - first) / params.framerate,
            'first': first, 'last': last, 'changed': first != 0 or last != count}
