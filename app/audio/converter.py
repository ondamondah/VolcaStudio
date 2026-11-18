from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

import soundfile as sf


IMPORT_EXTENSIONS = (".wav", ".aif", ".aiff", ".aifc", ".mp3", ".m4a", ".flac", ".ogg", ".mp4", ".mov")


def convert_pcm_wav(source, destination, *, rate=None, channels=None, timeout=15):
    if sys.platform == "darwin":
        encoding = f"LEI16@{rate}" if rate else "LEI16"
        command = ["/usr/bin/afconvert", "-f", "WAVE", "-d", encoding]
        if channels:
            command.extend(["-c", str(channels)])
        command.extend([str(source), str(destination)])
    else:
        from imageio_ffmpeg import get_ffmpeg_exe

        command = [get_ffmpeg_exe(), "-nostdin", "-v", "error", "-y",
                   "-i", str(source), "-map", "0:a:0", "-c:a", "pcm_s16le"]
        if rate:
            command.extend(["-ar", str(rate)])
        if channels:
            command.extend(["-ac", str(channels)])
        command.extend(["-f", "wav", str(destination)])
    subprocess.run(command, check=True, capture_output=True, timeout=timeout)


def prepare_playback_wav(source, destination):
    convert_pcm_wav(source, destination, rate=44100)


class AudioConverter:
    def __init__(self):
        self.temp_dir = TemporaryDirectory(
            prefix="volcastudio_"
        )

    def prepare(self, source):
        source = Path(source)

        destination = Path(self.temp_dir.name) / f"{uuid4().hex}.wav"
        try:
            try:
                audio = sf.SoundFile(source)
            except sf.LibsndfileError:
                convert_pcm_wav(source, destination, timeout=120)
                if sf.info(destination).frames <= 0:
                    raise ValueError("The file contains no audio.")
                return str(destination)

            with audio:
                if audio.frames <= 0:
                    raise ValueError("The file contains no audio.")
                if audio.format == "WAV" and audio.subtype == "PCM_16":
                    return str(source)
                with sf.SoundFile(destination, "w", audio.samplerate, audio.channels,
                                  format="WAV", subtype="PCM_16") as output:
                    for block in audio.blocks(blocksize=65536, dtype="float32", always_2d=True):
                        output.write(block)
            return str(destination)
        except subprocess.TimeoutExpired as exc:
            destination.unlink(missing_ok=True)
            raise ValueError("Audio decoding timed out.") from exc
        except subprocess.CalledProcessError as exc:
            destination.unlink(missing_ok=True)
            raise ValueError("Could not read an audio track from this file.") from exc
        except Exception:
            destination.unlink(missing_ok=True)
            raise
