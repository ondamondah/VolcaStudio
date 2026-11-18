from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from uuid import uuid4

import soundfile as sf


IMPORT_EXTENSIONS = (".wav", ".aif", ".aiff", ".aifc", ".mp3", ".m4a", ".flac", ".ogg", ".mp4", ".mov")


def prepare_playback_wav(source, destination):
    subprocess.run(
        ["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@44100", str(source), str(destination)],
        check=True, capture_output=True, timeout=15,
    )


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
                subprocess.run(
                    ["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16", str(source), str(destination)],
                    check=True, capture_output=True, timeout=120,
                )
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
