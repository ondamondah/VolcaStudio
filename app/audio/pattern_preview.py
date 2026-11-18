"""Mix a single pass of a Volca pattern for local playback."""

import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from app.audio.converter import prepare_playback_wav
from app.device.pattern_steps import STEP_COUNT, read_steps

_RATE = 44100


def required_slots(pattern):
    active, parts = read_steps(pattern)
    slots = {slot for slot, mask in parts if mask & active}
    if not slots:
        raise ValueError("There are no triggers on active steps.")
    return slots


def render_pattern_preview(pattern, paths, bpm, destination, *, empty_slots=frozenset()):
    if not 30 <= bpm <= 300:
        raise ValueError("Choose a tempo between 30 and 300 BPM.")
    required = required_slots(pattern)
    missing = sorted(required - paths.keys() - empty_slots)
    if missing:
        raise ValueError("Missing audio for Volca slots " + ", ".join(f"{slot:03d}" for slot in missing))

    active, parts = read_steps(pattern)
    steps = [step for step in range(STEP_COUNT) if active & (1 << step)]
    frames_per_step = _RATE * 60 / (bpm * 4)
    mix = np.zeros(round(len(steps) * frames_per_step), dtype=np.float32)
    audio = {}
    with tempfile.TemporaryDirectory(prefix="VolcaStudio-pattern-audio-") as folder:
        converted = Path(folder) / "converted.wav"
        for slot in required & paths.keys():
            source = paths[slot]
            if sf.info(str(source)).samplerate != _RATE:
                prepare_playback_wav(source, converted)
                source = converted
            frames, _ = sf.read(str(source), frames=len(mix), dtype="float32", always_2d=True)
            audio[slot] = frames.mean(axis=1)

    for position, step in enumerate(steps):
        start = round(position * frames_per_step)
        for slot, mask in parts:
            if slot in audio and mask & (1 << step):
                sample = audio[slot][:len(mix) - start]
                mix[start:start + len(sample)] += sample
    peak = np.abs(mix).max()
    if peak > 1:
        mix /= peak
    sf.write(str(destination), mix, _RATE, subtype="PCM_16")
