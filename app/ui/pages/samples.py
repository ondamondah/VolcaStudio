from app.library.storage import SampleLibrary
from app.ui.components.sample_editor import SampleEditor
from app.ui.components.optimize_dialog import OptimizeDialog
from app.ui.components.file_drop_list import FileDropList
from app.audio.converter import AudioConverter
from app.audio.editor import trim_wav, normalize_wav
from time import monotonic
from PySide6.QtCore import QTimer
from app.audio.player import AudioPlayer
from app.ui.components.waveform import WaveformWidget
import wave
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QListWidget, QMessageBox, QLineEdit, QInputDialog
)


class SamplesPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.samples = []
        self.library = SampleLibrary()
        self.converter = AudioConverter()
        self.player = AudioPlayer()

        self.playback_started = 0.0
        self.playback_duration = 0.0

        self.playhead_timer = QTimer(self)
        self.playhead_timer.setInterval(30)
        self.playhead_timer.timeout.connect(
            self.update_playhead
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Sample Manager")
        title.setObjectName("title")
        layout.addWidget(title)

        subtitle = QLabel("Drop WAV files into the library, or use Import WAV (multiple files supported).")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)

        toolbar = QHBoxLayout()

        self.import_button = QPushButton("+ Import WAV")
        self.import_button.clicked.connect(self.import_samples)
        toolbar.addWidget(self.import_button)
        self.optimize_button = QPushButton("Optimize…")
        self.optimize_button.setToolTip("Remove edge silence and compare before saving a copy")
        self.optimize_button.clicked.connect(self.optimize_selected)
        toolbar.addWidget(self.optimize_button)
        self.rename_button = QPushButton("Rename…")
        self.rename_button.setToolTip("Change the sample name in the library and all sets")
        self.rename_button.clicked.connect(self.rename_selected)
        toolbar.addWidget(self.rename_button)
        toolbar.addStretch()

        layout.addLayout(toolbar)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search samples...")
        self.search_input.textChanged.connect(self.filter_samples)
        layout.addWidget(self.search_input)

        self.sample_list = FileDropList()
        self.sample_list.files_dropped.connect(self.import_paths)
        self.sample_list.setToolTip("Drop one or more WAV files from Finder here")
        layout.addWidget(self.sample_list)
        # Load saved samples
        for sample in self.library.samples:
            if not Path(sample["path"]).is_file():
                continue

            self.samples.append(sample)

            name = sample["name"]
            if sample.get("converted"):
                name += "  [Converted]"

            self.sample_list.addItem(name)


        self.waveform = WaveformWidget()
        layout.addWidget(self.waveform)


        controls = QHBoxLayout()

        self.play_button = QPushButton("▶ Play")
        self.play_button.clicked.connect(self.play_selected)
        controls.addWidget(self.play_button)

        self.stop_button = QPushButton("■ Stop")
        self.stop_button.clicked.connect(self.stop_playback)
        controls.addWidget(self.stop_button)

        self.trim_button = QPushButton("✂ Edit…")
        self.trim_button.clicked.connect(self.edit_selected)
        controls.addWidget(self.trim_button)

        self.normalize_button = QPushButton("Normalize")
        self.normalize_button.clicked.connect(self.normalize_selected)
        controls.addWidget(self.normalize_button)

        self.delete_button = QPushButton("Delete Sample")
        self.delete_button.clicked.connect(self.delete_selected)
        controls.addWidget(self.delete_button)

        layout.addLayout(controls)

        self.info = QLabel("No sample selected")
        self.info.setObjectName("muted")
        layout.addWidget(self.info)

        self.sample_list.currentRowChanged.connect(
            self.show_sample_info
        )

        footer = QLabel(
            "Local sample library"
        )
        footer.setObjectName("muted")
        layout.addWidget(footer)

    def refresh_library(self):
        self.stop_playback()
        row = self.sample_list.currentRow()
        selected = self.samples[row]['id'] if 0 <= row < len(self.samples) else None
        self.sample_list.blockSignals(True)
        self.sample_list.clear()
        self.samples = [s for s in self.library.samples if Path(s['path']).is_file()]
        for sample in self.samples:
            label = sample['name'] + ("  [Converted]" if sample.get('converted') else "")
            self.sample_list.addItem(label)
        self.filter_samples(self.search_input.text())
        target = next((i for i, s in enumerate(self.samples)
                       if s['id'] == selected and not self.sample_list.item(i).isHidden()), -1)
        self.sample_list.setCurrentRow(target)
        self.sample_list.blockSignals(False)
        self.show_sample_info(target)

    def rename_selected(self):
        index = self.sample_list.currentRow()
        if index < 0 or self.sample_list.item(index).isHidden():
            QMessageBox.information(self, "Rename sample", "Select a sample first.")
            return
        sample = self.samples[index]
        name, ok = QInputDialog.getText(
            self, "Rename sample", "New name:", text=sample['name']
        )
        if not ok:
            return
        try:
            self.library.rename(sample['id'], name)
            label = sample['name']
            if sample.get('converted'):
                label += "  [Converted]"
            self.sample_list.item(index).setText(label)
            self.filter_samples(self.search_input.text())
            if self.sample_list.item(index).isHidden():
                self.sample_list.setCurrentRow(-1)
        except Exception as exc:
            QMessageBox.warning(self, "Could not rename sample", str(exc))

    def edit_selected(self):
        index = self.sample_list.currentRow()
        if index < 0 or self.sample_list.item(index).isHidden():
            QMessageBox.information(self, "Edit", "Select a sample first.")
            return
        self.stop_playback()
        dialog = SampleEditor(self.samples[index], self.library, self)
        try:
            dialog.exec()
        finally:
            dialog.cleanup()
            dialog.deleteLater()
            self.show_sample_info(index)

    def optimize_selected(self):
        index = self.sample_list.currentRow()
        if index < 0 or self.sample_list.item(index).isHidden():
            QMessageBox.information(self, "Optimize", "Select a sample first.")
            return
        self.stop_playback()
        dialog = None
        try:
            dialog = OptimizeDialog(self.samples[index], self.library, self)
            dialog.exec()
            if dialog.saved_sample is not None:
                sample = dialog.saved_sample
                self.samples.append(sample)
                self.sample_list.addItem(sample["name"])
                self.search_input.clear()
                self.sample_list.setCurrentRow(len(self.samples) - 1)
        except Exception as exc:
            QMessageBox.warning(self, "Optimization error", str(exc))
        finally:
            if dialog is not None:
                dialog.cleanup()
                dialog.deleteLater()

    def import_samples(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import WAV samples",
            "",
            "WAV files (*.wav *.WAV)"
        )

        self.import_paths(paths)

    def import_paths(self, paths):
        errors = []
        for path in paths:
            try:
                prepared = self.converter.prepare(path)
                sample = self.library.add(path, prepared)

                self.samples.append(sample)

                name = sample["name"]
                if sample.get("converted"):
                    name += "  [Converted]"

                self.sample_list.addItem(name)

            except Exception as exc:
                errors.append(f"{Path(path).name}: {exc}")
        self.filter_samples(self.search_input.text())
        if errors:
            QMessageBox.warning(self, "Import errors", "Could not import:\n" + "\n".join(errors))

    def show_sample_info(self, index):
        if index < 0 or index >= len(self.samples):
            self.info.setText("No sample selected")
            return

        sample = self.samples[index]
        self.waveform.load_file(sample['path'])

        self.info.setText(
            f"{sample['sample_rate']} Hz · "
            f"{sample['channels']} channel(s) · "
            f"{sample['duration']:.2f} seconds"
        )

    def play_selected(self):
        index = self.sample_list.currentRow()

        if index < 0:
            return

        try:
            self.player.play(self.samples[index]["path"])
            self.playback_duration = self.samples[index]["duration"]
            self.playback_started = monotonic()
            self.waveform.set_playhead(0.0)
            self.playhead_timer.start()
        except Exception as exc:
            QMessageBox.warning(
                self, "Playback error", str(exc)
            )

    def update_playhead(self):
        if not self.player.is_playing():
            self.stop_playback()
            return

        if self.playback_duration <= 0:
            return

        elapsed = monotonic() - self.playback_started
        position = elapsed / self.playback_duration
        self.waveform.set_playhead(position)

    def stop_playback(self):
        self.playhead_timer.stop()
        self.player.stop()
        self.waveform.set_playhead(0.0)

    def closeEvent(self, event):
        self.player.stop()
        super().closeEvent(event)

    def trim_selected(self):
        index = self.sample_list.currentRow()
        if index < 0:
            QMessageBox.information(
                self, "Trim", "Select a sample first."
            )
            return

        start, end = self.waveform.get_selection()

        if end - start < 0.00001:
            QMessageBox.information(
                self, "Trim", "Select a region on the waveform."
            )
            return

        source = self.samples[index]["path"]

        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Save trimmed sample",
            str(Path(source).with_name(
                Path(source).stem + "_trimmed.wav"
            )),
            "WAV files (*.wav)"
        )

        if not destination:
            return

        try:
            self.stop_playback()
            trim_wav(source, destination, start, end)
            QMessageBox.information(
                self, "Trim", "Trimmed sample saved successfully."
            )
        except Exception as exc:
            QMessageBox.warning(
                self, "Trim error", str(exc)
            )

    def normalize_selected(self):
        index = self.sample_list.currentRow()

        if index < 0:
            QMessageBox.information(
                self, "Normalize", "Select a sample first."
            )
            return

        source = self.samples[index]["path"]

        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Save normalized sample",
            str(Path(source).with_name(
                Path(source).stem + "_normalized.wav"
            )),
            "WAV files (*.wav)"
        )

        if not destination:
            return

        try:
            self.stop_playback()
            normalize_wav(source, destination)
            QMessageBox.information(
                self, "Normalize", "Normalized sample saved."
            )
        except Exception as exc:
            QMessageBox.warning(
                self, "Normalize error", str(exc)
            )

    def filter_samples(self, text):
        query = text.strip().casefold()

        for index in range(self.sample_list.count()):
            item = self.sample_list.item(index)
            item.setHidden(query not in item.text().casefold())

    def delete_selected(self):
        index = self.sample_list.currentRow()

        if index < 0 or index >= len(self.samples):
            QMessageBox.information(
                self, "Delete Sample", "Select a sample first."
            )
            return

        sample = self.samples[index]

        answer = QMessageBox.question(
            self,
            "Delete Sample",
            f"Remove '{sample['name']}' from the VolcaStudio library?\n\n"
            "The original file will not be deleted.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if answer != QMessageBox.Yes:
            return

        try:
            self.stop_playback()
            self.library.remove(sample["id"])

            self.samples.pop(index)
            self.sample_list.takeItem(index)

            self.waveform.peaks = []
            self.waveform.set_playhead(0.0)
            self.waveform.update()
            self.info.setText("No sample selected")

        except Exception as exc:
            QMessageBox.warning(
                self, "Delete error", str(exc)
            )
