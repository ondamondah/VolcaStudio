"""Exercise startup imports and Windows audio preparation without USB hardware."""
from pathlib import Path
from tempfile import TemporaryDirectory
import wave

from PySide6.QtWidgets import QApplication

from app.device.volca_sample2 import _prepare
from app.library.storage import SampleLibrary
from app.ui.components.sample_editor import SampleEditor


if __name__ == "__main__":
    from unittest.mock import patch

    app = QApplication([])
    with TemporaryDirectory() as folder, patch.object(Path, "home", return_value=Path(folder)):
        source = Path(folder) / "Test sample.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 44100)
        frames, pcm = _prepare(source)
        assert frames == 31250 and len(pcm) == frames * 2
        library = SampleLibrary()
        editor = SampleEditor(dict(name=source.name, path=str(source), duration=1), library)
        editor.set_mode("slice")
        editor.slice_evenly(4)
        editor.add_slices.click()
        assert len(library.samples) == 4
        editor.cleanup()
