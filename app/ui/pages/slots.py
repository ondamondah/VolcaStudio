from app.ui.translations import tr, translated, translated_action
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import os
import shutil
import subprocess
import tempfile

from PySide6.QtCore import QItemSelectionModel, QSignalBlocker, Qt, QTimer, QSize, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication, QDialog, QProgressDialog, QListWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy,
    QPushButton, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QMessageBox, QSplitter, QLineEdit, QListWidgetItem, QFileDialog, QProgressBar, QComboBox, QInputDialog, QMenu,
)

from app.core.device_worker import device_worker_argv
from app.device.volca_sample2 import MAX_TRANSFER_SECONDS, estimated_sample_memory
from app.ui.device_tasks import VOLCA_NO_REPLY, is_identify_timeout, wait_for_device
from app.ui.preferences import startup_options
from app.ui.transfer_history import save_confirmed_transfers
from app.ui.theme import ROW_SELECTION_BACKGROUND, is_light, set_warning
from app.library.slots import SampleSlots
from app.library.named_sets import NamedSets
from app.ui.components.slot_drag import LibraryDragList, SlotDropTable
from app.library.sets import read_set, write_set
from app.library.bundle import export_bundle, import_bundle, preview_bundle
from app.audio.converter import IMPORT_EXTENSIONS, AudioConverter
from app.audio.editor import normalize_wav
from app.audio.optimizer import optimize_silence
from app.audio.player import AudioPlayer
from app.ui.components.optimize_dialog import OptimizeDialog
from app.ui.components.overlay_scrollbar import OverlayScrollBar
from app.ui.components.sample_editor import SampleEditor
from app.ui.components.sample_operation_result import show_sample_result
from app.ui.components.slot_waveform import SlotWaveformDelegate, WAVEFORM_PATH
from app.ui.components.transfer_preview import TransferPreviewTable


_ROW_INSET = 6
_PLAY_SIZE = QSize(29, 22)


class SampleNameLabel(QLabel):
    def __init__(self, name):
        super().__init__(name)
        self.setTextFormat(Qt.PlainText)
        self.full_name = name
        self.setAccessibleName(name)
        self.setToolTip(name)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.contentsRect().width()
        self.setText(self.fontMetrics().elidedText(self.full_name, Qt.ElideRight, width))


class SlotsPage(QWidget):
    device_slots_changing = Signal()

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.converter = AudioConverter()
        self.player = AudioPlayer()
        self.playing_slot = None
        self.playing_library_id = None
        self.library_buttons = {}
        self.row_buttons = {}
        self.playback_timer = QTimer(self)
        self.playback_timer.setInterval(100)
        self.playback_timer.timeout.connect(self.check_playback)
        self.store = None
        self.load_error = None
        self.named_sets = None
        try:
            self.store = SampleSlots(library.root)
            self.named_sets = NamedSets(self.store)
        except Exception as exc:
            self.load_error = str(exc)
            self.store = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        title = translated(QLabel(), "Samples")
        title.setObjectName("title")
        layout.addWidget(title)
        title.setToolTip("Double-click a sample to edit. Drag samples to slots to assign them.")

        panes = QSplitter(Qt.Horizontal)
        panes.setObjectName("samplePanes")
        panes.setHandleWidth(9)
        panes.setChildrenCollapsible(False)
        library_panel = QWidget()
        library_layout = QVBoxLayout(library_panel)
        library_layout.setContentsMargins(0, 0, 8, 0)
        library_heading = translated(QLabel(), "Library")
        library_heading.setObjectName("section")
        library_layout.addWidget(library_heading)
        library_controls = QHBoxLayout()
        import_button = translated(QPushButton(), "Import audio…")
        import_button.clicked.connect(self.import_samples)
        library_controls.addWidget(import_button)
        sample_actions = translated(QPushButton(), "Actions")
        sample_actions.setProperty("menuButton", True)
        sample_menu = QMenu(sample_actions)
        self.library_actions = [
            translated_action(sample_menu, "Edit sample…", self.edit_library_sample),
            translated_action(sample_menu, "Rename…", self.rename_library_sample),
            translated_action(sample_menu, "Optimize…", self.optimize_library_sample),
            sample_menu.addAction("Export normalized WAV…", self.normalize_library_sample),
            translated_action(sample_menu, "Delete from library…", self.delete_library_sample),
        ]
        sample_menu.addSeparator()
        self.send_selected_library_action = translated_action(
            sample_menu, "Send selected samples to Volca…", self.send_selected_library_samples,
        )
        self.send_library_action = translated_action(sample_menu, "Send Library to Volca…", self.send_library)
        self.optimize_all_action = translated_action(sample_menu, "Optimize Library…", self.optimize_library)
        sample_menu.addSeparator()
        self.clear_library_action = translated_action(sample_menu, "Clear Library…", self.clear_library)
        sample_actions.setMenu(sample_menu)
        library_controls.addWidget(sample_actions)
        library_layout.addLayout(library_controls)
        self.search = QLineEdit()
        translated(self.search, "Search library…", "setPlaceholderText")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_library)
        library_layout.addWidget(self.search)
        self.library_list = LibraryDragList()
        self.library_list.setObjectName("sampleLibrary")
        self.library_scroll = OverlayScrollBar(self.library_list)
        self.library_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.library_list.files_dropped.connect(self.import_paths)
        translated(self.library_list, "Drop audio files or MP4 and MOV videos here.", "setToolTip")
        self.library_empty = QLabel()
        self.library_empty.setObjectName("muted")
        self.library_empty.setAlignment(Qt.AlignCenter)
        self.library_empty.setWordWrap(True)
        self.library_empty.setAttribute(Qt.WA_TransparentForMouseEvents)
        empty_layout = QVBoxLayout(self.library_list.viewport())
        empty_layout.addWidget(self.library_empty)
        library_layout.addWidget(self.library_list)
        self.bulk_button = translated(QPushButton(), "Add to free slots")
        self.bulk_button.setToolTip("Select with ⌘ / Shift. Fill from the selected slot, then wrap to 000; occupied slots are skipped.")
        self.bulk_button.clicked.connect(self.assign_multiple)
        library_layout.addWidget(self.bulk_button)
        panes.addWidget(library_panel)

        self.grid = SlotDropTable(200, 3)
        self.grid.setObjectName("sampleSlotTable")
        self.slot_scroll = OverlayScrollBar(self.grid)
        self.grid.setItemDelegateForColumn(1, SlotWaveformDelegate(self.grid))
        self.grid.slot_moved.connect(self.move_slot)
        self.grid.sample_dropped.connect(self.assign_dropped)
        self.grid.samples_dropped.connect(self.assign_group)
        translated(self.grid, ("Slot", "Sample", "Time"), "setHorizontalHeaderLabels")
        self.grid.verticalHeader().hide()
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.grid.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.grid.setColumnWidth(0, 84)
        self.grid.setColumnWidth(2, 64)
        _, theme, density = startup_options()
        self.compact = density == "compact"
        self.theme = theme
        self.grid.verticalHeader().setDefaultSectionSize(self._row_height)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.grid.setWordWrap(False)
        self.grid.setTextElideMode(Qt.ElideRight)
        self.grid.setShowGrid(False)
        for number in range(200):
            row = number
            for column in range(3):
                item = QTableWidgetItem()
                if column == 0:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif column == 2:
                    item.setTextAlignment(Qt.AlignCenter)
                self.grid.setItem(row, column, item)
            button = self._preview_button(f"slot {number:03d}")
            button.clicked.connect(lambda checked=False, n=number: self.toggle_slot(n))
            self.row_buttons[number] = button
            controls = QWidget()
            controls.setObjectName("slotControls")
            controls_layout = QHBoxLayout(controls)
            controls_layout.setContentsMargins(_ROW_INSET, 0, 0, 0)
            controls_layout.addWidget(button)
            controls_layout.addStretch()
            self.grid.setCellWidget(row, 0, controls)
        slots_panel = QWidget()
        slots_layout = QVBoxLayout(slots_panel)
        slots_layout.setContentsMargins(0, 0, 0, 0)
        slots_heading = translated(QLabel(), "Set slots")
        slots_heading.setObjectName("section")
        slots_layout.addWidget(slots_heading)
        self.capacity_labels = {}
        self.capacity_bars = {}
        meters = QHBoxLayout()
        for key in ("slots", "duration", "memory"):
            label = QLabel()
            label.setWordWrap(True)
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            self.capacity_labels[key] = label
            self.capacity_bars[key] = bar
            meter = QVBoxLayout()
            meter.setSpacing(4)
            meter.addWidget(label)
            meter.addWidget(bar)
            meters.addLayout(meter, 1)
        slots_layout.addLayout(meters)
        self.capacity_labels["memory"].setToolTip(
            "Estimated mono PCM at 31,250 Hz / 16-bit; 1 MB here = 1,048,576 bytes. "
            "Excludes device overhead. Source WAV size may differ. "
            "Repeated samples count once per slot. Not a reading from the device."
        )
        self.set_summary = QLabel()
        self.set_summary.setWordWrap(True)
        slots_layout.addWidget(self.set_summary)
        slots_layout.addWidget(self.grid)
        panes.addWidget(slots_panel)
        panes.setSizes([260, 540])
        layout.addWidget(panes, 1)

        controls = QHBoxLayout()
        self.clear_button = translated(QPushButton(), "Clear selected slot")
        self.set_picker = QComboBox()
        self.set_picker.setMinimumContentsLength(12)
        self.set_picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.set_picker.setToolTip("Current set · changes save automatically")
        controls.addWidget(translated(QLabel(), "Current set"))
        controls.addWidget(self.set_picker, 1)
        self.set_menu_button = translated(QPushButton(), "Sets")
        self.set_menu_button.setProperty("menuButton", True)
        menu = QMenu(self.set_menu_button)
        menu.addSection("Manage sets")
        translated_action(menu, "New empty set…", self.new_named_set)
        translated_action(menu, "Rename current set…", self.rename_named_set)
        translated_action(menu, "Duplicate current set…", self.duplicate_named_set)
        self.delete_set_action = translated_action(menu, "Delete current set…", self.delete_named_set)
        menu.addSection("Import & export")
        menu.addAction("Export selected slot as WAV…", self.export_slot_wav)
        menu.addAction("Export set with audio (ZIP)…", self.export_set_bundle)
        menu.addAction("Import set with audio (ZIP)…", self.import_set_bundle)
        menu.addAction("Export current set…", self.save_set)
        menu.addAction("Import into current set…", self.load_set)
        self.set_menu_button.setMenu(menu)
        controls.addWidget(self.set_menu_button)
        self.set_picker.setEnabled(self.named_sets is not None)
        self.set_menu_button.setEnabled(self.named_sets is not None)
        self.refresh_set_picker()
        self.set_picker.currentTextChanged.connect(self.switch_named_set)
        layout.insertLayout(1, controls)
        actions = QHBoxLayout()
        self.send_button = translated(QPushButton(), "Send to Volca")
        self.send_button.setProperty("menuButton", True)
        self.send_button.setObjectName("primaryAction")
        send_menu = QMenu(self.send_button)
        self.send_slot_action = translated_action(send_menu, "Send selected slot…", self.send_selected_sample)
        self.send_set_action = translated_action(send_menu, "Preview set transfer…", self.preview_set_transfer)
        self.send_button.setMenu(send_menu)
        actions.addWidget(self.send_button)
        actions.addStretch()
        self.undo_button = translated(QPushButton(), "Undo")
        self.undo_button.setToolTip("Undo the last slot change in this set. Resets when switching sets or restarting.")
        self.undo_button.clicked.connect(self.undo_slots)
        actions.addWidget(self.undo_button)
        actions.addWidget(self.clear_button)
        slots_layout.addLayout(actions)
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.grid.currentCellChanged.connect(self.stop_playback)
        self.grid.currentCellChanged.connect(self.update_selection)
        self.grid.cellDoubleClicked.connect(self.edit_slot)
        self.library_list.currentItemChanged.connect(self.update_selection)
        self.library_list.itemSelectionChanged.connect(self.update_selection)
        self.library_list.itemDoubleClicked.connect(self.edit_library_sample)
        self.clear_button.clicked.connect(self.clear_selected)
        self.grid.setCurrentCell(0, 0)
        self.refresh()

    def refresh_set_picker(self):
        self.set_picker.blockSignals(True)
        self.set_picker.clear()
        if self.named_sets is not None:
            self.set_picker.addItems(list(self.named_sets.state['sets']))
            self.set_picker.setCurrentText(self.named_sets.state['current'])
        self.set_picker.blockSignals(False)
        self.delete_set_action.setEnabled(
            self.named_sets is not None and len(self.named_sets.state['sets']) > 1
        )

    def switch_named_set(self, name):
        if self.named_sets is None or name == self.named_sets.state['current']:
            return
        self.stop_playback()
        try:
            self.store.slots = self.named_sets.switch(name, self.store.slots)
            self.store.clear_undo()
            self.render_slots()
            self.status.setText(f"{name} · Auto-save")
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not switch set"), str(exc))
        self.refresh_set_picker()

    def new_named_set(self):
        name, ok = QInputDialog.getText(self, "New empty set", "Set name:")
        if not ok:
            return
        try:
            self.stop_playback()
            self.store.slots = self.named_sets.create(name, self.store.slots)
            self.store.clear_undo()
            self.refresh_set_picker()
            self.render_slots()
            self.status.setText("New empty set · Previous set saved")
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not create set"), str(exc))

    def rename_named_set(self):
        name, ok = QInputDialog.getText(self, "Rename set", "New name:", text=self.named_sets.state['current'])
        if not ok or name.strip() == self.named_sets.state['current']:
            return
        try:
            self.named_sets.rename(name)
            self.refresh_set_picker()
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not rename set"), str(exc))

    def duplicate_named_set(self):
        current = self.named_sets.state['current']
        name, ok = QInputDialog.getText(
            self, "Duplicate set", "Name for the copy:", text=current + " copy"
        )
        if not ok:
            return
        try:
            self.stop_playback()
            self.store.slots = self.named_sets.duplicate(name, self.store.slots)
            self.store.clear_undo()
            self.refresh_set_picker()
            self.render_slots()
            self.status.setText("Set copied · Assignments are independent; sample audio is shared")
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not duplicate set"), str(exc))

    def delete_named_set(self):
        current = self.named_sets.state['current']
        answer = QMessageBox.question(
            self, "Delete set",
            f'Delete set "{current}"? This cannot be undone.\n'
            "Samples will remain in the library.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.stop_playback()
            self.store.slots = self.named_sets.delete_current()
            self.store.clear_undo()
            self.refresh_set_picker()
            self.render_slots()
            self.status.setText("Set deleted · Samples remain in the library")
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not delete set"), str(exc))

    def edit_slot(self, row, column):
        if column != 1 or not 0 <= row < 200:
            return
        sample = self.sample_for_slot(row)
        if sample is None:
            return
        self.open_sample_editor(sample)

    def edit_library_sample(self, *_):
        sample = self.selected_library_sample()
        if sample is not None:
            self.open_sample_editor(sample)

    def open_sample_editor(self, sample):
        if not Path(sample["path"]).is_file():
            QMessageBox.warning(self, tr("Missing sample"), tr("The sample file is missing and cannot be edited."))
            return
        self.stop_playback()
        dialog = None
        previous_count = len(self.library.samples)
        try:
            dialog = SampleEditor(sample, self.library, self, send_fragments=self._send_library_samples)
            dialog.exec()
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not open editor"), str(exc))
        finally:
            if dialog is not None:
                dialog.cleanup()
                dialog.deleteLater()
            added = self.library.samples[previous_count:]
            if added:
                self.refresh()
                self.search.clear()
                self.focus_library_sample(added[0]["id"])
                added_ids = {sample["id"] for sample in added}
                for row in range(self.library_list.count()):
                    item = self.library_list.item(row)
                    if item.data(Qt.UserRole) in added_ids:
                        item.setSelected(True)
            else:
                self.render_slots()

    def import_samples(self):
        patterns = " ".join(f"*{extension}" for item in IMPORT_EXTENSIONS for extension in (item, item.upper()))
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("Import audio"), "", f"{tr('Audio and video files')} ({patterns})",
        )
        self.import_paths(paths)

    def import_paths(self, paths):
        if not paths:
            return
        errors = []
        added = 0
        progress = QProgressDialog(tr("Importing audio…"), "", 0, len(paths), self)
        progress.setCancelButton(None)
        progress.setWindowFlag(Qt.WindowCloseButtonHint, False)
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setValue(0)
        progress.show()
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                for index, path in enumerate(paths, 1):
                    prepared = None
                    try:
                        prepared = wait_for_device(executor.submit(self.converter.prepare, path))
                        self.library.add(path, prepared)
                        added += 1
                    except Exception as exc:
                        errors.append(f"{Path(path).name}: {tr(str(exc))}")
                    finally:
                        if prepared is not None and Path(prepared) != Path(path):
                            Path(prepared).unlink(missing_ok=True)
                    progress.setValue(index)
        finally:
            progress.close()
        if added:
            self.refresh()
            self.status.setText(tr("Imported {count} samples · Saved to library").format(count=added))
        if errors:
            QMessageBox.warning(self, tr("Could not import samples"), "\n".join(errors))

    def selected_library_sample(self):
        items = self.selected_library_items()
        if len(items) != 1:
            return None
        sample_id = items[0].data(Qt.UserRole)
        return next((sample for sample in self.library.samples if sample["id"] == sample_id), None)

    def rename_library_sample(self):
        sample = self.selected_library_sample()
        if sample is None:
            return
        name, accepted = QInputDialog.getText(self, "Rename sample", "New name:", text=sample["name"])
        if accepted:
            try:
                self.library.rename(sample["id"], name)
                self.refresh()
            except Exception as exc:
                QMessageBox.warning(self, tr("Could not rename sample"), str(exc))

    def optimize_library_sample(self):
        sample = self.selected_library_sample()
        if sample is None:
            return
        self.stop_playback()
        dialog = None
        try:
            dialog = OptimizeDialog(sample, self.library, self)
            dialog.exec()
            if dialog.saved_sample is not None:
                self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not optimize sample"), str(exc))
        finally:
            if dialog is not None:
                dialog.cleanup()
                dialog.deleteLater()

    def optimize_library(self):
        samples = list(self.library.samples)
        if not samples or QMessageBox.question(
            self, "Optimize library",
            f"Trim near-silent edges from all {len(samples)} samples?\n\n"
            "Original audio is kept in each sample’s edit history.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.stop_playback()
        progress = QProgressDialog("Optimizing library…", tr("Stop"), 0, len(samples), self)
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.show()
        changed = unchanged = 0
        errors = []
        try:
            with tempfile.TemporaryDirectory(prefix="VolcaStudio-optimize-") as folder, ThreadPoolExecutor(max_workers=1) as executor:
                output = Path(folder) / "optimized.wav"
                for index, sample in enumerate(samples, 1):
                    if progress.wasCanceled():
                        break
                    progress.setLabelText(f"Optimizing {index} of {len(samples)}…")
                    try:
                        result = wait_for_device(executor.submit(optimize_silence, sample["path"], output))
                        if result["changed"]:
                            self.library.apply_edit(sample["id"], output)
                            changed += 1
                        else:
                            unchanged += 1
                    except Exception as exc:
                        errors.append(f"{sample['name']}: {exc}")
                    if not progress.wasCanceled():
                        progress.setValue(index)
        finally:
            progress.close()
        if changed:
            self.refresh()
        remaining = len(samples) - changed - unchanged - len(errors)
        result = f"{changed} optimized · {unchanged} already clean"
        if remaining:
            result += f" · {remaining} not processed"
        if errors:
            QMessageBox.warning(self, tr("Library optimization"), result + "\n\n" + "\n".join(errors))
        else:
            QMessageBox.information(self, tr("Library optimization"), result)

    def normalize_library_sample(self):
        sample = self.selected_library_sample()
        if sample is None:
            return
        source = Path(sample["path"])
        path, _ = QFileDialog.getSaveFileName(
            self, "Export normalized WAV", str(source.with_name(source.stem + "_normalized.wav")), "WAV files (*.wav)",
        )
        if not path:
            return
        try:
            if Path(path).resolve() == source.resolve():
                raise ValueError("Choose a different location from the library file.")
            self.stop_playback()
            normalize_wav(source, path)
            self.status.setText("Normalized WAV exported.")
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not normalize sample"), str(exc))

    def delete_library_sample(self):
        sample = self.selected_library_sample()
        if sample is None or QMessageBox.question(
            self, "Delete sample", f"Remove '{sample['name']}' from the local library?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            self.stop_playback()
            self.library.remove(sample["id"])
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not delete sample"), str(exc))

    def clear_library(self):
        count = len(self.library.samples)
        if not count or self.named_sets is None:
            return
        answer = QMessageBox.question(
            self, "Clear sample library",
            f"Clear all {count} samples and their assignments in local sets?\n\n"
            "VolcaStudio will save a recovery copy of the audio, library and sets.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.stop_playback()
        try:
            recovery = self.library.clear(self.named_sets)
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not clear library"), str(exc))
            return
        state = self.named_sets.state
        self.store.slots = dict(state["sets"][state["current"]])
        self.store.clear_undo()
        self.search.clear()
        self.refresh()
        self.status.setText("Library cleared · Recovery copy saved")
        QMessageBox.information(self, tr("Library cleared"), f"Recovery copy saved in:\n{recovery}")

    def selected_number(self):
        return self.grid.currentRow()

    @property
    def _row_height(self):
        return 28 if self.compact else 34

    @staticmethod
    def _preview_button(label):
        button = QPushButton()
        button.setObjectName("samplePlay")
        button.setFixedSize(_PLAY_SIZE)
        button.setProperty("previewLabel", label)
        SlotsPage._set_preview_playing(button, False)
        return button

    @staticmethod
    def _set_preview_playing(button, playing):
        action = tr("Stop {sample}" if playing else "Play {sample}").format(
            sample=button.property("previewLabel")
        )
        button.setText("■" if playing else "▶")
        button.setToolTip(action)
        button.setAccessibleName(action)

    def set_compact(self, compact):
        self.compact = compact
        self.grid.verticalHeader().setDefaultSectionSize(self._row_height)
        for row in range(self.library_list.count()):
            item = self.library_list.item(row)
            widget = self.library_list.itemWidget(item)
            if widget is not None:
                self._size_library_row(item, widget)

    def _size_library_row(self, item, widget):
        item.setSizeHint(QSize(0, self._row_height))
        layout = widget.layout()
        layout.setContentsMargins(_ROW_INSET, 0, 8, 0)
        layout.setSpacing(8)

    def refresh(self):
        self.stop_playback()
        self.library_buttons.clear()
        selected_id = self.library_sample_id()
        self.library_list.clear()
        for sample in self.library.samples:
            if Path(sample["path"]).is_file():
                item = QListWidgetItem()
                item.setData(Qt.UserRole + 1, sample["name"])
                item.setData(Qt.UserRole, sample["id"])
                item.setToolTip(sample["name"])
                self.library_list.addItem(item)
                row_widget = QWidget()
                row_widget.setStyleSheet("background: transparent;")
                row_layout = QHBoxLayout(row_widget)
                self._size_library_row(item, row_widget)
                name = SampleNameLabel(sample["name"])
                name.setAttribute(Qt.WA_TransparentForMouseEvents)
                button = self._preview_button(sample["name"])
                button.clicked.connect(lambda checked=False, sid=sample["id"]: self.toggle_library(sid))
                self.library_buttons[sample["id"]] = button
                row_layout.addWidget(button)
                row_layout.addWidget(name, 1)
                self.library_list.setItemWidget(item, row_widget)
                if sample["id"] == selected_id:
                    self.library_list.setCurrentItem(item)
        self.filter_library(self.search.text())
        self.render_slots()
        if self.load_error:
            self.status.setText("Could not load slots. Existing slots.json was preserved. " + self.load_error)
        else:
            self.status.setText(tr("Changes save automatically"))

    def library_sample_id(self):
        item = self.library_list.currentItem()
        return item.data(Qt.UserRole) if item is not None and not item.isHidden() else None

    def filter_library(self, text):
        query = text.strip().casefold()
        has_match = False
        for row in range(self.library_list.count()):
            item = self.library_list.item(row)
            matches = query in item.data(Qt.UserRole + 1).casefold()
            item.setHidden(not matches)
            has_match |= matches
        self.library_empty.setText(tr("No matching samples" if query else "Import or drop audio here"))
        self.library_empty.setVisible(not has_match)
        self.update_selection()

    def focus_library_sample(self, sample_id):
        for row in range(self.library_list.count()):
            item = self.library_list.item(row)
            if item.data(Qt.UserRole) == sample_id:
                if item.isHidden():
                    self.search.clear()
                self.library_list.setCurrentItem(item, QItemSelectionModel.ClearAndSelect)
                self.library_list.scrollToItem(item)
                self.library_list.setFocus()
                return

    def slot_description(self, number):
        sample_id = self.store.get(number) if self.store else None
        if sample_id is None:
            return tr("Empty")
        sample = next((s for s in self.library.samples if s["id"] == sample_id), None)
        if sample is None:
            return tr("Missing sample")
        if not Path(sample["path"]).is_file():
            return "Missing file: " + sample["name"]
        return sample["name"]

    def update_set_summary(self):
        if self.store is None:
            self.set_summary.setText("Slots unavailable")
            for key in self.capacity_bars:
                self.capacity_bars[key].setValue(0)
                self.capacity_labels[key].setText("Unavailable")
            return
        occupied = 0
        missing = 0
        total = 0.0
        for number in range(200):
            if self.store.get(number) is None:
                continue
            occupied += 1
            sample = self.sample_for_slot(number)
            if sample is None or not Path(sample["path"]).is_file():
                missing += 1
            else:
                total += sample["duration"]
        memory_mb = total * 31250 * 2 / (1024 * 1024)
        self.set_capacity("slots", occupied, 200,
            f"Slots · {occupied}/200")
        self.set_capacity("duration", total, 130,
            f"Audio · {total:.1f}/130 s")
        self.set_capacity("memory", memory_mb, 8,
            f"Memory · {memory_mb:.2f}/8 MB")
        notes = []
        if total > 130:
            notes.append(f"Duration exceeds limit by {total - 130:.1f} s")
        if memory_mb > 8:
            notes.append(f"Memory exceeds limit by {memory_mb - 8:.2f} MB")
        if missing:
            notes.append(f"{missing} missing sample(s): duration and memory are incomplete")
        self.set_summary.setText(" · ".join(notes))
        self.set_summary.setVisible(bool(notes))
        set_warning(self.set_summary, bool(notes))

    def set_capacity(self, key, used, maximum, text):
        color = "#a6686c"
        ratio = used / maximum
        if ratio > 1:
            color = "#e77c7c"
        elif ratio >= 0.9:
            color = "#e6b566"
        bar = self.capacity_bars[key]
        bar.setValue(min(1000, max(0, round(ratio * 1000))))
        bar.setStyleSheet(
            "QProgressBar {background: #303030; border: none; border-radius: 4px;}"
            f"QProgressBar::chunk {{background: {color}; border-radius: 4px;}}"
        )
        bar.setAccessibleName(text)
        self.capacity_labels[key].setText(text)

    def render_slots(self):
        self.undo_button.setEnabled(self.store is not None and self.store.can_undo)
        empty_foreground = QBrush(QColor("#66565c" if is_light(self.theme) else "#a6a6a6"))
        for number in range(200):
            row = number
            description = self.slot_description(number)
            sample = self.sample_for_slot(number)
            self.grid.item(row, 0).setText(f"{number:03d}")
            self.grid.item(row, 1).setData(Qt.UserRole, self.store.get(number) if self.store else None)
            self.grid.item(row, 1).setData(WAVEFORM_PATH, sample["path"] if sample else None)
            empty = self.store is None or self.store.get(number) is None
            foreground = empty_foreground if empty else QBrush()
            for column in (0, 1, 2):
                self.grid.item(row, column).setForeground(foreground)
            name_item = self.grid.item(row, 1)
            name_item.setText("" if empty else description)
            name_item.setData(Qt.AccessibleTextRole, description)
            name_item.setToolTip(tr("Drop a sample here") if empty else description)
            self.grid.item(row, 2).setText(f"{sample['duration']:.2f} s" if sample else "")
            available = sample is not None and Path(sample["path"]).is_file()
            self.row_buttons[number].setEnabled(available)
            self.row_buttons[number].setVisible(available)
        self.update_set_summary()
        self.update_selection()

    def update_selection(self, *_):
        number = self.selected_number()
        valid = self.store is not None and 0 <= number < 200
        self.clear_button.setEnabled(valid and self.store.get(number) is not None)
        sample = self.selected_sample() if valid else None
        can_send_slot = sample is not None and Path(sample["path"]).is_file()
        can_preview = self.store is not None and any(self.store.slots.values())
        self.send_slot_action.setText(tr("Send selected slot {number:03d}…").format(number=number) if valid else tr("Send selected slot…"))
        self.send_slot_action.setEnabled(can_send_slot)
        self.send_set_action.setEnabled(can_preview)
        self.send_button.setEnabled(can_send_slot or can_preview)
        selected = self.selected_library_items()
        self.bulk_button.setEnabled(valid and bool(selected))
        self.bulk_button.setText(f"{tr('Add to free slots')} ({len(selected)})" if selected else tr("Add to free slots"))
        for action in self.library_actions:
            action.setEnabled(len(selected) == 1)
        self.send_selected_library_action.setEnabled(bool(selected))
        self.send_library_action.setEnabled(bool(self.library.samples))
        self.optimize_all_action.setEnabled(bool(self.library.samples))
        self.clear_library_action.setEnabled(bool(self.library.samples) and self.named_sets is not None)
        selection = "#ecd5dc" if is_light(self.theme) else ROW_SELECTION_BACKGROUND
        for row in range(self.library_list.count()):
            item = self.library_list.item(row)
            widget = self.library_list.itemWidget(item)
            if widget is not None:
                background = selection if item.isSelected() else "transparent"
                widget.setStyleSheet(f"background: {background};")
    def selected_sample(self):
        return self.sample_for_slot(self.selected_number())

    def sample_for_slot(self, number):
        if self.store is None or not 0 <= number < 200:
            return None
        sample_id = self.store.get(number)
        return next((s for s in self.library.samples if s["id"] == sample_id), None)

    def toggle_library(self, sample_id):
        if self.playing_library_id == sample_id:
            self.stop_playback()
            return
        self.stop_playback()
        sample = next((s for s in self.library.samples if s["id"] == sample_id), None)
        if sample is None or not Path(sample["path"]).is_file():
            self.refresh()
            return
        try:
            self.player.play(sample["path"])
        except Exception as exc:
            QMessageBox.warning(self, tr("Playback error"), str(exc))
            return
        self.playing_library_id = sample_id
        button = self.library_buttons[sample_id]
        self._set_preview_playing(button, True)
        self.playback_timer.start()

    def toggle_slot(self, number):
        if self.playing_slot == number:
            self.stop_playback()
            return
        self.grid.setCurrentCell(number, 1)
        self.play_selected()

    def play_selected(self):
        self.stop_playback()
        sample = self.selected_sample()
        if sample is None or not Path(sample["path"]).is_file():
            self.update_selection()
            return
        try:
            self.player.play(sample["path"])
        except Exception as exc:
            self.stop_playback()
            QMessageBox.warning(self, tr("Playback error"), str(exc))
            return
        self.playing_slot = self.selected_number()
        button = self.row_buttons[self.playing_slot]
        self._set_preview_playing(button, True)
        self.playback_timer.start()

    def stop_playback(self, *_):
        self.playback_timer.stop()
        self.player.stop()
        if self.playing_slot is not None:
            button = self.row_buttons[self.playing_slot]
            self._set_preview_playing(button, False)
        self.playing_slot = None
        if self.playing_library_id is not None:
            button = self.library_buttons.get(self.playing_library_id)
            if button is not None:
                self._set_preview_playing(button, False)
        self.playing_library_id = None

    def check_playback(self):
        if not self.player.is_playing():
            self.stop_playback()

    def hideEvent(self, event):
        self.stop_playback()
        super().hideEvent(event)

    def selected_library_items(self):
        return [self.library_list.item(row) for row in range(self.library_list.count())
                if self.library_list.item(row).isSelected() and not self.library_list.item(row).isHidden()]

    def assign_multiple(self):
        ids = [item.data(Qt.UserRole) for item in self.selected_library_items()]
        self.assign_group(self.selected_number(), ids)

    def assign_group(self, start, ids):
        if self.store is None or not ids or not 0 <= start < 200:
            return
        available = {s["id"] for s in self.library.samples if Path(s["path"]).is_file()}
        if any(sample_id not in available for sample_id in ids):
            QMessageBox.warning(self, tr("Missing sample"), "Some selected files are missing. No slots were changed.")
            return
        order = list(range(start, 200)) + list(range(start))
        free = [number for number in order if self.store.get(number) is None]
        if len(free) < len(ids):
            QMessageBox.information(self, tr("Not enough free slots"),
                f"Selected: {len(ids)}. Free slots: {len(free)}.\nSelect fewer samples. No slots were changed.")
            return
        updated = dict(self.store.slots)
        for number, sample_id in zip(free, ids):
            updated[str(number)] = sample_id
        self.stop_playback()
        try:
            self.store.replace_all(updated)
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not save assignments"), str(exc))
            return
        self.render_slots()
        next_slot = free[len(ids)] if len(free) > len(ids) else free[len(ids) - 1]
        self.grid.setCurrentCell(next_slot, 1)
        self.grid.scrollToItem(self.grid.item(next_slot, 1))
        self.status.setText(f"Added {len(ids)} samples · Saved · Occupied slots preserved")

    def move_slot(self, source, target):
        if self.store is None or source == target:
            return
        if not 0 <= source < 200 or not 0 <= target < 200:
            return
        if self.store.get(source) is None:
            return
        updated = dict(self.store.slots)
        occupied = updated[str(target)] is not None
        updated[str(source)], updated[str(target)] = updated[str(target)], updated[str(source)]
        self.stop_playback()
        try:
            self.store.replace_all(updated)
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not move sample"), str(exc))
            self.status.setText("Save failed. Previous assignments preserved.")
            return
        self.render_slots()
        self.grid.setCurrentCell(target, 1)
        action = "Swapped" if occupied else "Moved"
        self.status.setText(f"{action}: {source:03d} → {target:03d} · Saved")

    def assign_dropped(self, number, sample_id):
        self.assign_sample(number, sample_id)

    def assign_sample(self, number, sample_id):
        if self.store is None or not 0 <= number < 200:
            return False
        sample = next((s for s in self.library.samples if s["id"] == sample_id), None)
        if sample is None or not Path(sample["path"]).is_file():
            return False
        previous = self.store.get(number)
        if previous == sample_id:
            return False
        if previous is not None:
            if QMessageBox.question(self, tr("Replace assignment"),
                    f"Replace the assignment in slot {number:03d}?\nThe sample file stays in the library.",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return False
        if self.save_change(number, sample_id):
            self.grid.setCurrentCell(number, 1)
            return True
        return False

    def sets_folder(self):
        folder = self.library.root / "Sets"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def import_set_bundle(self):
        if self.store is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Import set with audio"), str(self.library.root), "VolcaStudio archive (*.zip)"
        )
        if not path:
            return
        self.stop_playback()
        try:
            name, count, assignments = preview_bundle(path)
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not open archive"), str(exc))
            return
        preview = QMessageBox(self)
        preview.setWindowTitle(tr("Import set with audio"))
        preview.setTextFormat(Qt.PlainText)
        preview.setText(f"“{name}” · {count} samples · {len(assignments)} occupied slots")
        preview.setInformativeText("Import this archive as a new set?")
        if assignments:
            preview.setDetailedText("\n".join(f"{slot:03d}   {sample}" for slot, sample in assignments))
        cancel = preview.addButton(QMessageBox.Cancel)
        import_button = preview.addButton("Import set", QMessageBox.AcceptRole)
        preview.setDefaultButton(cancel)
        preview.exec()
        if preview.clickedButton() != import_button:
            return
        try:
            name, count = import_bundle(path, self.library, self.named_sets, self.store)
        except Exception as exc:
            self.refresh()
            QMessageBox.warning(self, tr("Could not import archive"), str(exc))
            return
        self.refresh_set_picker()
        self.refresh()
        self.status.setText(f"Imported {name} · {count} samples · Saved as a new set")

    def preview_set_transfer(self):
        if self.store is None:
            return
        assigned = [(number, self.sample_for_slot(number))
                    for number in range(200) if self.store.get(number) is not None]
        if not assigned:
            QMessageBox.information(self, tr("Transfer preview"), tr("The current set has no assigned samples."))
            return
        state = self._read_transfer_targets(list(range(200)))
        if state is not None:
            self._send_with_preview(assigned, *state, allow_remap=True)

    def send_dropped_sample(self, number, sample_id):
        sample = next((item for item in self.library.samples if item["id"] == sample_id), None)
        if sample is None:
            return
        state = self._read_transfer_targets([number])
        if state is not None:
            self._send_with_preview([(number, sample)], *state)

    def send_selected_library_samples(self):
        selected = {item.data(Qt.UserRole) for item in self.selected_library_items()}
        self._send_library_samples([sample for sample in self.library.samples if sample["id"] in selected])

    def send_library(self):
        self._send_library_samples(list(self.library.samples))

    def _send_library_samples(self, samples):
        if not samples:
            return
        state = self._read_transfer_targets(list(range(200)))
        if state is None:
            return
        device, free_memory = state
        free_slots = iter(number for number in range(200) if device[number][1] is None)
        assigned = [(None if self._sample_transfer_issue(sample) else next(free_slots, None), sample)
                    for sample in samples]
        self._send_with_preview(assigned, device, free_memory)

    @staticmethod
    def _sample_transfer_issue(sample):
        if sample is None or not Path(sample["path"]).is_file():
            return tr("Missing local audio")
        if not 0 < sample["duration"] <= MAX_TRANSFER_SECONDS:
            return f"Requires audio up to {MAX_TRANSFER_SECONDS} seconds"
        return None

    def _read_transfer_targets(self, numbers):
        while True:
            try:
                return self._read_transfer_targets_once(numbers)
            except Exception as exc:
                if not is_identify_timeout(exc):
                    QMessageBox.warning(self, tr("Could not read Volca slots"), str(exc))
                    return None
                message = QMessageBox(QMessageBox.Warning, tr("Could not read Volca slots"),
                                      tr(VOLCA_NO_REPLY), QMessageBox.Cancel, self)
                retry = message.addButton(tr("Retry connection"), QMessageBox.AcceptRole)
                message.setDefaultButton(retry)
                message.exec()
                if message.clickedButton() != retry:
                    return None

    def _read_transfer_targets_once(self, numbers):
        progress = QProgressDialog(tr("Reading target slots and free memory…"), "", 0, 0, self)
        progress.setCancelButton(None)
        progress.setWindowFlag(Qt.WindowCloseButtonHint, False)
        progress.setWindowModality(Qt.ApplicationModal)
        progress.show()
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                slots, free_memory = wait_for_device(executor.submit(
                    self._transfer_state_in_process, numbers,
                ))
            device = {number: (name, duration) for number, name, duration in slots}
            if any(number not in device for number in numbers):
                raise ValueError("Volca did not return all requested slots.")
        finally:
            progress.close()
        return device, free_memory

    def _send_with_preview(self, assigned, device, free_memory, *, allow_remap=False):
        original_slots = {number: number for number, _ in assigned}
        confirmed_transfers = {}

        def remember(target, name):
            device_name = (name.encode("ascii", "replace")[:24] or b"Sample").split(b"\0", 1)[0]
            confirmed_transfers[original_slots[target]] = (target, device_name.decode("ascii").strip())
            save_confirmed_transfers(confirmed_transfers)

        while True:
            selected = self._show_transfer_preview(assigned, device, free_memory, allow_remap=allow_remap)
            if selected is None:
                return
            ready, required_memory, previous_targets = selected
            original_slots = {target: original_slots[previous] for target, previous in previous_targets.items()}
            remaining = self.send_sample_batch(
                ready, required_memory, on_confirmed=remember if allow_remap else None,
            )
            if not remaining:
                return
            samples_by_audio = {
                (sample["path"], sample["name"]): sample for _, sample in assigned if sample is not None
            }
            assigned = [(number, samples_by_audio[path, name]) for number, path, name in remaining]
            numbers = list(range(200)) if allow_remap else [number for number, _ in assigned]
            state = self._read_transfer_targets(numbers)
            if state is None:
                return
            device, free_memory = state

    def _show_transfer_preview(self, assigned, device, free_memory, *, allow_remap=False):
        targets = [number for number, _ in assigned]
        issues = [self._sample_transfer_issue(sample) for _, sample in assigned]
        costs = [
            estimated_sample_memory(sample["duration"]) if issue is None else 0
            for (_, sample), issue in zip(assigned, issues)
        ]

        def row_issue(row):
            target = targets[row]
            if issues[row]:
                return issues[row]
            if target is None:
                return tr("No free slot on Volca")
            name, duration = device[target]
            if duration is not None:
                return tr("Occupied on Volca: {name}").format(name=name or tr("Unnamed sample"))
            return ""

        def row_status(row):
            issue = row_issue(row)
            if issue:
                return issue
            ready = tr("Ready · ~{memory} KiB").format(memory=costs[row] // 1024)
            original = assigned[row][0]
            if targets[row] != original:
                return tr("From {slot:03d} · {status}").format(slot=original, status=ready)
            return ready

        lines = [
            (number, sample["name"] if sample else tr("Unnamed sample"), row_status(row))
            for row, (number, sample) in enumerate(assigned)
        ]
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Volca Sample 2 · Transfer preview"))
        dialog.resize(670, 520)
        layout = QVBoxLayout(dialog)
        heading = translated(QLabel(), "Send samples")
        heading.setObjectName("section")
        layout.addWidget(heading)
        summary = QLabel()
        summary.setObjectName("muted")
        layout.addWidget(summary)
        memory = QLabel()
        memory.setObjectName("transferMemory")
        memory.setWordWrap(True)
        listing = TransferPreviewTable(lines)
        for row in range(len(assigned)):
            if not row_issue(row):
                item = listing.item(row, 0)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
        layout.addWidget(listing, 1)
        close = translated(QPushButton(), "Cancel")
        close.clicked.connect(dialog.reject)
        send = QPushButton()
        send.setObjectName("primaryAction")
        send.clicked.connect(dialog.accept)
        change = QPushButton(tr("Choose free slot…"))
        change.setVisible(allow_remap)
        auto_assign = QPushButton(tr("Find free slots"))
        auto_assign.setVisible(allow_remap)

        def free_targets():
            reserved = set(targets)
            return sorted(number for number, (_, duration) in device.items()
                          if duration is None and number not in reserved)

        def blocked_targets():
            return [row for row in range(len(targets)) if not issues[row] and row_issue(row)]

        def ready_rows():
            return [row for row in range(len(assigned)) if not row_issue(row)]

        def selected_rows(rows):
            return [row for row in rows if listing.item(row, 0).checkState() == Qt.Checked]

        def update_send():
            ready = ready_rows()
            selected = selected_rows(ready)
            count = len(selected)
            required = sum(costs[row] for row in selected)
            summary.setText(tr("{ready} ready · {blocked} blocked").format(
                ready=len(ready), blocked=len(assigned) - len(ready),
            ))
            send.setText(tr("Send selected ({count})…").format(count=count))
            fits = required <= free_memory
            detail = ""
            if not fits:
                excess_kib = (required - free_memory + 1023) // 1024
                detail = "\n" + tr("Over by ~{excess} KiB. Uncheck samples to fit.").format(
                    excess=excess_kib,
                )
            memory.setText(tr("{count} selected · ~{needed} KiB needed · {free} KiB free").format(
                count=count, needed=required // 1024, free=free_memory // 1024,
            ) + detail)
            set_warning(memory, not fits)
            send.setEnabled(count > 0 and fits)
            select_all.setEnabled(bool(ready))
            clear_all.setEnabled(bool(selected))
            row = listing.currentRow()
            available = allow_remap and bool(free_targets())
            change.setEnabled(available and row >= 0 and not issues[row])
            offer_remap = available and bool(blocked_targets())
            auto_assign.setEnabled(offer_remap)
            auto_assign.setText(tr("Use suggested free slots") if offer_remap else tr("Find free slots"))
            if offer_remap and not ready:
                summary.setText(tr("Target slots are occupied. Choose Use suggested free slots to continue."))
            primary = offer_remap and not ready
            for button, highlighted in ((auto_assign, primary), (send, not primary)):
                name = "primaryAction" if highlighted else ""
                if button.objectName() != name:
                    button.setObjectName(name)
                    button.style().unpolish(button)
                    button.style().polish(button)
                    button.update()

        def set_selection(state):
            with QSignalBlocker(listing):
                for row in ready_rows():
                    listing.item(row, 0).setCheckState(state)
            update_send()

        def change_target():
            row = listing.currentRow()
            if row < 0 or issues[row]:
                return
            options = [f"{number:03d}" for number in free_targets()]
            if not options:
                return
            value, accepted = QInputDialog.getItem(
                dialog, tr("Choose free slot…"), tr("Destination slot"), options, 0, False,
            )
            if not accepted:
                return
            with QSignalBlocker(listing):
                assign_target(row, int(value))
            update_send()

        def assign_target(row, target):
            targets[row] = target
            item = listing.item(row, 0)
            item.setText(f"{target:03d}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            status = listing.item(row, 2)
            status.setText(row_status(row))
            status.setToolTip(status.text())

        def assign_free_targets():
            with QSignalBlocker(listing):
                for row, target in zip(blocked_targets(), free_targets()):
                    assign_target(row, target)
            update_send()

        selection_bar = QHBoxLayout()
        selection_bar.addWidget(memory, 1)
        select_all = translated(QPushButton(), "Select all ready")
        select_all.clicked.connect(lambda: set_selection(Qt.Checked))
        clear_all = translated(QPushButton(), "Clear selection")
        clear_all.clicked.connect(lambda: set_selection(Qt.Unchecked))
        selection_bar.addWidget(select_all)
        selection_bar.addWidget(clear_all)
        layout.addLayout(selection_bar)
        actions = QHBoxLayout()
        change.clicked.connect(change_target)
        actions.addWidget(change)
        auto_assign.clicked.connect(assign_free_targets)
        actions.addWidget(auto_assign)
        for button in (select_all, clear_all, change, auto_assign, close, send):
            button.setAutoDefault(False)
        actions.addStretch()
        actions.addWidget(close)
        actions.addWidget(send)
        layout.addLayout(actions)

        listing.itemChanged.connect(update_send)
        listing.currentCellChanged.connect(update_send)
        update_send()
        if dialog.exec() != QDialog.Accepted or not send.isEnabled():
            return
        chosen = selected_rows(ready_rows())
        ready = [(targets[row], assigned[row][1]["path"], assigned[row][1]["name"]) for row in chosen]
        if QMessageBox.question(
            self, tr("Send samples to Volca"),
            f"Send {len(ready)} samples to the free slots shown in the preview?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        return ready, sum(costs[row] for row in chosen), {targets[row]: assigned[row][0] for row in chosen}

    @staticmethod
    def _transfer_state_in_process(numbers):
        command = (
            "import json, sys; from app.device.volca_sample2 import transfer_state; "
            "print(json.dumps(transfer_state(json.loads(sys.argv[1]))))"
        )
        return json.loads(SlotsPage._run_slot_command(command, json.dumps(numbers), timeout=120))

    @staticmethod
    def _send_sample_in_process(path, name, slot):
        command = (
            "import sys; from app.device.volca_sample2 import send_sample; "
            "print(send_sample(sys.argv[1], sys.argv[2], int(sys.argv[3]) if sys.argv[3] else None))"
        )
        result = SlotsPage._run_slot_command(command, str(path), name, "" if slot is None else str(slot), timeout=90)
        return int(result.strip())

    @staticmethod
    def _run_slot_command(command, *args, timeout):
        try:
            result = subprocess.run(
                device_worker_argv(command, *args),
                cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("The Volca operation timed out. Refresh the device slots before retrying.") from exc
        if result.returncode:
            lines = result.stderr.strip().splitlines()
            raise RuntimeError(lines[-1] if lines else "The transfer process closed unexpectedly.")
        return result.stdout

    def send_sample_batch(self, ready, required_memory, *, on_confirmed=None):
        self.stop_playback()
        state = self._read_transfer_targets([number for number, _, _ in ready])
        if state is None:
            return []
        device, free_memory = state
        occupied = [f"{number:03d}" for number, _, _ in ready if device[number][1] is not None]
        error = None
        if occupied:
            error = "Slots became occupied: " + ", ".join(occupied)
        elif required_memory > free_memory:
            error = f"Free memory changed to {free_memory // 1024} KiB."
        if error:
            return self._show_transfer_result(ready, 0, error)

        progress = QProgressDialog(tr("Preparing transfer…"), tr("Stop after current sample"), 0, len(ready), self)
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        confirmed = 0
        stopping = False
        unverified = None

        def request_stop():
            nonlocal stopping
            stopping = True
            progress.setCancelButton(None)
            progress.setLabelText(tr("Stopping after the current sample…"))
            progress.show()

        progress.canceled.connect(request_stop)
        progress.show()
        self.device_slots_changing.emit()
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                for number, path, name in ready:
                    if stopping:
                        break
                    progress.setLabelText(f"Sending and checking slot {number:03d}…")
                    try:
                        actual = wait_for_device(executor.submit(self._send_sample_in_process, path, name, number))
                        if actual != number:
                            raise RuntimeError(f"Volca reported slot {actual:03d}.")
                    except Exception as exc:
                        unverified = number
                        error = str(exc)
                        break
                    confirmed += 1
                    if on_confirmed is not None:
                        on_confirmed(number, name)
                    if not stopping:
                        progress.setValue(confirmed)
        finally:
            progress.canceled.disconnect(request_stop)
            progress.close()
        return self._show_transfer_result(ready, confirmed, error, unverified)

    def _show_transfer_result(self, ready, confirmed, error, unverified=None):
        slots = [number for number, _, _ in ready]
        if show_sample_result(self, self.status, slots, confirmed, error, unverified,
                              paused_title="Transfer paused", failed_title="Transfer stopped"):
            return ready[confirmed:]
        return []

    def send_selected_sample(self):
        sample = self.selected_sample()
        if sample is None:
            QMessageBox.information(self, tr("Send to Volca"), tr("Select a slot with an available sample."))
            return
        issue = self._sample_transfer_issue(sample)
        if issue:
            QMessageBox.information(self, tr("Send to Volca"), issue)
            return
        selected = self.selected_number()
        same_slot = f"Use selected slot {selected:03d}"
        auto_slot = "Find a free slot (from 199 down)"
        mode, accepted = QInputDialog.getItem(
            self, "Destination on Volca Sample 2", "Choose the destination:",
            [same_slot, "Enter a different slot number", auto_slot], 0, False
        )
        if not accepted:
            return
        slot = selected
        if mode == "Enter a different slot number":
            slot, accepted = QInputDialog.getInt(
                self, "Send one sample to Volca Sample 2", "Destination slot on Volca:", selected, 0, 199,
            )
            if not accepted:
                return
        state = self._read_transfer_targets(list(range(200)))
        if state is None:
            return
        device, free_memory = state
        if mode == auto_slot:
            slot = next((number for number in range(199, -1, -1) if device[number][1] is None), None)
        self._send_with_preview([(slot, sample)], device, free_memory, allow_remap=True)

    def export_slot_wav(self):
        sample = self.selected_sample()
        if sample is None or not Path(sample["path"]).is_file():
            QMessageBox.information(self, tr("Export WAV"), tr("Select a slot with an available sample."))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export WAV for KORG Librarian",
            str(Path.home() / f"Volca-slot-{self.selected_number():03d}.wav"),
            "WAV files (*.wav)"
        )
        if not path:
            return
        destination = Path(path)
        source = Path(sample["path"])
        temporary = None
        try:
            if destination.resolve() == source.resolve():
                raise ValueError("Choose a different location from the library file.")
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as file:
                temporary = Path(file.name)
                with source.open("rb") as audio:
                    shutil.copyfileobj(audio, file)
            os.replace(temporary, destination)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, tr("Could not export WAV"), str(exc))
            return
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.status.setText("WAV exported. Choose a free device slot in KORG Librarian.")

    def export_set_bundle(self):
        if self.store is None:
            return
        try:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export set with audio", str(self.library.root / "VolcaStudio-set.zip"),
                "VolcaStudio archive (*.zip)"
            )
            if not path:
                return
            self.stop_playback()
            name = self.named_sets.state['current']
            count = export_bundle(path, name, self.store.slots, self.library.samples)
            self.status.setText(f"Exported {count} samples with slot assignments: {Path(path).name}")
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not export archive"), str(exc))

    def save_set(self):
        if self.store is None:
            return
        try:
            path, _ = QFileDialog.getSaveFileName(self, "Save set (assignments only)",
                str(self.sets_folder() / "My set.json"), "VolcaStudio sets (*.json)")
            if not path:
                return
            write_set(path, self.store.slots)
            self.status.setText(f"Saved: {Path(path).name} · Uses samples from this library.")
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not save set"), str(exc))

    def load_set(self):
        if self.store is None:
            return
        try:
            path, _ = QFileDialog.getOpenFileName(self, tr("Load set"), str(self.sets_folder()), "VolcaStudio sets (*.json)")
            if not path:
                return
            slots = read_set(path)
            available = {s["id"] for s in self.library.samples if Path(s["path"]).is_file()}
            missing = sum(value is not None and value not in available for value in slots.values())
            message = f"Load '{Path(path).name}' and replace all current slot assignments?\nSample files will remain unchanged."
            if missing:
                message += f"\n{missing} slot(s) refer to missing samples; they will be marked as missing."
            if QMessageBox.question(self, tr("Load set"), message,
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self.stop_playback()
            write_set(self.sets_folder() / "Before last load.json", self.store.slots)
            self.store.replace_all(slots)
            self.render_slots()
            self.status.setText(f"Loaded: {Path(path).name} · Previous assignments saved as Before last load.json")
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not load set"), str(exc))

    def undo_slots(self):
        if self.store is None or not self.store.can_undo:
            return
        self.stop_playback()
        try:
            self.store.undo()
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not undo"), str(exc))
            self.status.setText("Undo failed. Current assignments preserved; you can retry.")
            return
        self.render_slots()
        self.status.setText("Last slot change undone · Saved")

    def clear_selected(self):
        if self.store is not None:
            self.save_change(self.selected_number(), None)

    def save_change(self, number, sample_id):
        self.stop_playback()
        try:
            if sample_id is None:
                self.store.clear(number)
            else:
                self.store.assign(number, sample_id)
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not save slots"), str(exc))
            self.status.setText("Save failed. The previous assignment was preserved.")
            return
        self.render_slots()
        self.status.setText(tr("Slot {number:03d} saved." if sample_id else
                               "Slot {number:03d} cleared. Sample file retained.").format(number=number))
        return True
