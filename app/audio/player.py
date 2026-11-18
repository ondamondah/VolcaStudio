from pathlib import Path
from tempfile import TemporaryDirectory
import wave
import simpleaudio as sa

from app.audio.converter import prepare_playback_wav


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
            self.playback = sa.play_buffer(
                audio.readframes(end - start), audio.getnchannels(),
                audio.getsampwidth(), audio.getframerate(),
            )

    def _play_wav(self, path):
        with wave.open(str(path), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frames = audio.readframes(audio.getnframes())

        self.playback = sa.play_buffer(
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
