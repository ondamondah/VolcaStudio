from pathlib import Path
from tempfile import TemporaryDirectory
import sys
from time import monotonic
import wave

if sys.platform == "win32":
    import winsound
else:
    import simpleaudio as sa

from app.audio.converter import prepare_playback_wav


class WindowsPlayback:
    def __init__(self, frames, channels, sample_width, sample_rate):
        self.folder = TemporaryDirectory(prefix="VolcaStudio-audio-")
        self.deadline = 0
        try:
            path = Path(self.folder.name) / "preview.wav"
            with wave.open(str(path), "wb") as output:
                output.setparams((channels, sample_width, sample_rate, 0, "NONE", "not compressed"))
                output.writeframes(frames)
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            self.deadline = monotonic() + len(frames) / (channels * sample_width * sample_rate)
        except Exception:
            self.folder.cleanup()
            raise

    def stop(self):
        winsound.PlaySound(None, 0)
        self.folder.cleanup()

    def is_playing(self):
        return monotonic() < self.deadline


def play_buffer(frames, channels, sample_width, sample_rate):
    if sys.platform == "win32":
        return WindowsPlayback(frames, channels, sample_width, sample_rate)
    return sa.play_buffer(frames, channels, sample_width, sample_rate)


class AudioPlayer:
    def __init__(self):
        self.playback = None

    def play(self, path):
        self.stop()
        with wave.open(str(path), "rb") as audio:
            needs_conversion = audio.getframerate() == 31250
        if needs_conversion:
            with TemporaryDirectory(prefix="VolcaStudio-playback-") as folder:
                destination = Path(folder) / "playback.wav"
                prepare_playback_wav(path, destination)
                self._play_wav(destination)
        else:
            self._play_wav(path)

    def play_region(self, path, start, end):
        self.stop()
        with wave.open(str(path), "rb") as audio:
            audio.setpos(start)
            self.playback = play_buffer(
                audio.readframes(end - start), audio.getnchannels(),
                audio.getsampwidth(), audio.getframerate(),
            )

    def _play_wav(self, path):
        with wave.open(str(path), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frames = audio.readframes(audio.getnframes())

        self.playback = play_buffer(
            frames,
            channels,
            sample_width,
            sample_rate
        )

    def stop(self):
        if self.playback is not None:
            self.playback.stop()
            self.playback = None

    def is_playing(self):
        return (
            self.playback is not None
            and self.playback.is_playing()
        )
