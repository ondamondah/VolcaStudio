from pathlib import Path
from tempfile import TemporaryDirectory
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox
from app.audio.optimizer import optimize_silence
from app.audio.player import AudioPlayer


class OptimizeDialog(QDialog):
    def __init__(self, sample, library, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Optimize sample')
        self.setMinimumWidth(460)
        self.sample = sample
        self.library = library
        self.saved_sample = None
        self.player = AudioPlayer()
        self.temporary = TemporaryDirectory(prefix='volcastudio_optimize_')
        self.preview = Path(self.temporary.name) / (Path(sample['name']).stem + ' [Optimized].wav')
        try:
            result = optimize_silence(sample['path'], self.preview)
        except Exception:
            self.temporary.cleanup()
            raise
        layout = QVBoxLayout(self)
        name = QLabel(sample['name']); name.setWordWrap(True); layout.addWidget(name)
        saved = result['before'] - result['after']
        percent = saved / result['before'] * 100
        size = lambda seconds: seconds * 31250 * 2 / 1024
        details = QLabel(
            f"Duration: {result['before']:.3f} → {result['after']:.3f} s\n"
            f"Estimated Volca memory: {size(result['before']):.1f} → {size(result['after']):.1f} KiB\n"
            f"Savings: {saved:.3f} s · {percent:.1f}%"
        )
        layout.addWidget(details)
        note = QLabel('Only near-silent edges are removed. Attack and tail margins are retained.\n'
                      'Listen before saving. Original and slot assignments remain unchanged.' if result['changed']
                      else 'No useful edge silence found. No changes needed.')
        note.setWordWrap(True); layout.addWidget(note)
        row = QHBoxLayout()
        for label, path in [('▶ Original', sample['path']), ('▶ Optimized', str(self.preview))]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, p=path: self.play(p))
            row.addWidget(button)
        stop = QPushButton('■ Stop'); stop.clicked.connect(self.player.stop); row.addWidget(stop)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.save_button = QPushButton('Save optimized copy')
        self.save_button.setEnabled(result['changed'])
        self.save_button.clicked.connect(self.save_copy)
        cancel = QPushButton('Close'); cancel.clicked.connect(self.reject)
        row.addWidget(self.save_button); row.addWidget(cancel); layout.addLayout(row)

    def play(self, path):
        try:
            self.player.play(path)
        except Exception as exc:
            QMessageBox.warning(self, 'Playback error', str(exc))

    def save_copy(self):
        self.player.stop()
        try:
            self.saved_sample = self.library.add(self.preview, self.preview)
        except Exception as exc:
            QMessageBox.warning(self, 'Could not save copy', str(exc))
            return
        self.accept()

    def done(self, result):
        self.player.stop()
        super().done(result)

    def cleanup(self):
        self.player.stop()
        self.temporary.cleanup()
