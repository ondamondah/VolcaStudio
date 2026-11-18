from __future__ import annotations

from app.ui.translations import retranslate, set_language, tr, translated, translated_action

from app import __version__
from app.core.device_worker import device_worker_argv
from app.device.volca_sample2 import EmptySampleSlotError, InvalidSlotReplyError

from concurrent.futures import ThreadPoolExecutor
import json
from hashlib import sha256
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from PySide6.QtCore import QEasingCurve, QVariantAnimation, QIODevice, QSaveFile, QSettings, QSignalBlocker, QSize, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QListWidget, QListWidgetItem, QProgressBar, QProgressDialog,
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMenu,
    QApplication, QDialog, QInputDialog, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout,
    QStackedWidget, QTableWidget, QTableWidgetItem, QWidget,
)

from app.core.logging_config import configure_logging
from app.audio.pattern_preview import render_pattern_preview, required_slots
from app.audio.player import AudioPlayer
from app.audio.converter import prepare_playback_wav
from app.device.midi import MidiService
from app.device.pattern_backup import load_pattern_backup
from app.device.pattern_steps import read_steps
from app.device.sample_restore import extract_snapshot_wavs, inspect_snapshot, load_init_pattern, load_samples, plan_pattern_samples
from app.device.wav_backup import WavBackup, list_backup_wavs
from app.ui.components.navigation_icons import navigation_icon
from app.ui.components.overlay_scrollbar import OverlayScrollBar
from app.ui.components.pattern_sample_slots import AUDIO_LABELS, PatternSampleSlotsDialog
from app.ui.components.pattern_steps import PatternStepsDialog
from app.ui.components.sample_operation_result import show_sample_result
from app.ui.components.slot_drag import DeviceDropButton
from app.ui.components.transfer_preview import TransferPreviewTable
from app.ui.components.volca_panel import VolcaPanel
from app.ui.device_tasks import VOLCA_NO_REPLY, is_identify_timeout, wait_for_device
from app.ui.preferences import SettingsPage, preferences, startup_options
from app.ui.theme import interface_style, is_light, set_warning
from app.library.storage import SampleLibrary
from app.ui.pages.slots import SlotsPage

logger = logging.getLogger(__name__)

# Keep the local preview available in code while its place in Patterns is deferred.
_SHOW_PATTERN_PREVIEW = False


class MainWindow(QMainWindow):
    midi_message_received = Signal(str)

    def __init__(self):
        super().__init__()
        configure_logging()

        self.setWindowTitle("VolcaStudio")
        self.setMinimumSize(900, 620)
        self.resize(1100, 750)

        self.midi = MidiService(self._incoming_message)
        self.midi_message_received.connect(self._append_incoming)

        self._build_ui()
        self.refresh_ports()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._check_ports)
        self.timer.start(2000)

    def _build_ui(self):
        language, theme, density = startup_options()
        set_language(language)
        self.theme = theme
        self.density = density
        self.compact = density == "compact"
        self.setStyleSheet(interface_style(theme, density))

        central = QWidget()
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        sidebar = QFrame()
        sidebar.setObjectName("panel")
        sidebar.setFixedWidth(210)

        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 20, 14, 20)
        side_layout.setSpacing(6)

        logo = QLabel("VolcaStudio")
        logo.setObjectName("title")
        side_layout.addWidget(logo)

        version = QLabel(f"VERSION {__version__}")
        version.setObjectName("muted")
        side_layout.addWidget(version)

        self.device_button = translated(DeviceDropButton(), "Device")
        self.device_button.clicked.connect(
            lambda: self.switch_page(1)
        )
        side_layout.addWidget(self.device_button)

        self.samples_button = translated(QPushButton(), "Samples")
        self.samples_button.clicked.connect(
            lambda: self.switch_page(0)
        )
        side_layout.addWidget(self.samples_button)

        self.patterns_button = translated(QPushButton(), "Patterns")
        self.patterns_button.clicked.connect(lambda: self.switch_page(2))
        side_layout.addWidget(self.patterns_button)

        self.settings_button = translated(QPushButton(), "Settings")
        self.settings_button.clicked.connect(lambda: self.switch_page(3))
        side_layout.addWidget(self.settings_button)

        self.navigation_buttons = (
            (self.device_button, "device"),
            (self.samples_button, "samples"),
            (self.patterns_button, "patterns"),
            (self.settings_button, "settings"),
        )
        for button, _ in self.navigation_buttons:
            button.setObjectName("navigation")
            button.setCheckable(True)
            button.setIconSize(QSize(18, 18))

        side_layout.addStretch()

        self.connection_badge = translated(QLabel(), "● Volca not detected")
        self.connection_badge.setStyleSheet(
            "color: #e6b566; font-weight: 600;"
        )
        side_layout.addWidget(self.connection_badge)
        root.addWidget(sidebar)

        content = QVBoxLayout()
        content.setSpacing(0)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 16, 0)
        toolbar.addStretch()
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("themeButton")
        self.theme_button.setFixedSize(30, 30)
        self.theme_button.setIconSize(QSize(18, 18))
        self.theme_button.clicked.connect(self._toggle_theme)
        toolbar.addWidget(self.theme_button)
        content.addLayout(toolbar)
        self.pages = QStackedWidget()
        content.addWidget(self.pages, 1)
        root.addLayout(content, 1)

        self.library = SampleLibrary()
        self.slots_page = SlotsPage(self.library)
        self.pages.addWidget(self.slots_page)

        self.device_page = QWidget()
        self.pages.addWidget(self.device_page)
        self._device_pattern_usage = None
        self._build_device_page()
        self.slots_page.device_slots_changing.connect(self._mark_device_slots_changed)

        self.pages.addWidget(self._build_patterns_page())
        self.settings_page = SettingsPage(self.library.root, self._select_language, self._select_density)
        self.pages.addWidget(self.settings_page)
        self._apply_theme()
        QApplication.styleHints().colorSchemeChanged.connect(self._system_theme_changed)
        self.switch_page(0)

    def _select_language(self, language):
        preferences().setValue("language", language)
        set_language(language)
        retranslate(self)
        self.slots_page.update_selection()
        self._update_pattern_actions()
        self._refresh_device_pattern_usage()
        light = is_light(self.theme)
        label = tr("Switch to dark theme" if light else "Switch to light theme")
        self.theme_button.setAccessibleName(label)
        self.theme_button.setToolTip(label)

    def _select_density(self, density):
        preferences().setValue("density", density)
        self.density = density
        self.compact = density == "compact"
        self.setStyleSheet(interface_style(self.theme, density))
        self.slots_page.set_compact(self.compact)
        self.device_table.verticalHeader().setDefaultSectionSize(28 if self.compact else 30)
        self._resize_pattern_rows()

    def _toggle_theme(self):
        self._select_theme("dark" if is_light(self.theme) else "light")

    def _select_theme(self, theme):
        self.theme = theme
        preferences().setValue("theme", self.theme)
        self._apply_theme()

    def _system_theme_changed(self, _):
        if self.theme == "system":
            self._apply_theme()

    def _apply_theme(self):
        self.setStyleSheet(interface_style(self.theme, self.density))
        light = is_light(self.theme)
        for button, section in self.navigation_buttons:
            button.setIcon(navigation_icon(section, light=light))
        self.theme_button.setIcon(navigation_icon("theme", light=light))
        label = tr("Switch to dark theme" if light else "Switch to light theme")
        self.theme_button.setAccessibleName(label)
        self.theme_button.setToolTip(label)
        self.slots_page.theme = self.theme
        self.slots_page.render_slots()
        self._style_connection_badge(self.device_read_button.isEnabled())

    def _style_connection_badge(self, detected):
        if is_light(self.theme):
            color = "#326854" if detected else "#805c26"
        else:
            color = "#9ac5b1" if detected else "#c7aa78"
        self.connection_badge.setStyleSheet(f"color: {color}; font-weight: 600;")

    def switch_page(self, index):
        if self.pages.currentIndex() != index:
            self.slots_page.stop_playback()
            self._stop_device_preview()
            self._stop_pattern_preview()
        self.pages.setCurrentIndex(index)
        self.samples_button.setChecked(index == 0)
        self.device_button.setChecked(index == 1)
        self.patterns_button.setChecked(index == 2)
        self.settings_button.setChecked(index == 3)

    def _build_patterns_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        title = translated(QLabel(), "Patterns")
        title.setObjectName("title")
        layout.addWidget(title)
        backup = translated(QPushButton(), "Back up patterns…")
        backup.clicked.connect(self.backup_device_patterns)
        backup.setEnabled(False)
        open_backup = translated(QPushButton(), "Open backup…")
        read_live = self.read_live_patterns_button = translated(QPushButton(), "Read from Volca")
        read_live.setEnabled(False)
        read_live.setObjectName("primaryAction")
        export = translated(QPushButton(), "Save pattern…")
        export.setEnabled(False)
        compare = translated(QPushButton(), "Compare with Volca")
        compare.setEnabled(False)
        buttons = QHBoxLayout()
        buttons.addWidget(read_live)
        buttons.addWidget(open_backup)
        buttons.addStretch()
        buttons.addWidget(backup)
        layout.addLayout(buttons)

        archive_label = QLabel()
        archive_label.setObjectName("muted")
        archive_label.setTextFormat(Qt.PlainText)
        archive_label.setWordWrap(True)
        archive_label.hide()
        layout.addWidget(archive_label)

        view = QStackedWidget()
        placeholder = QFrame()
        placeholder.setObjectName("panel")
        placeholder_layout = QVBoxLayout(placeholder)
        empty = translated(QLabel(), "Open a backup to view its patterns, or read them from Volca.")
        empty.setAlignment(Qt.AlignCenter)
        empty.setWordWrap(True)
        empty.setObjectName("muted")
        placeholder_layout.addWidget(empty)
        view.addWidget(placeholder)
        listing = QListWidget()
        listing.setObjectName("patternList")
        listing.overlay_scroll = OverlayScrollBar(listing)
        view.addWidget(listing)
        layout.addWidget(view, 1)
        restore = translated(QPushButton(), "Restore pattern…")
        restore.setEnabled(False)
        edit = translated(QPushButton(), "Pattern actions")
        edit.setProperty("menuButton", True)
        edit_menu = QMenu(edit)
        steps = translated_action(edit_menu, "View steps…")
        edit_separator = edit_menu.addSeparator()
        rename = translated_action(edit_menu, "Rename on Volca…")
        reset = translated_action(edit_menu, "Reset to Init Pattern…")
        edit.setMenu(edit_menu)
        edit.hide()
        archive_actions = QWidget()
        actions = QHBoxLayout(archive_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addWidget(compare)
        actions.addStretch()
        actions.addWidget(export)
        actions.addWidget(restore)
        actions.addWidget(edit)
        archive_actions.hide()
        layout.addWidget(archive_actions)
        patterns = []
        archive_name = ""
        live_patterns = {}
        showing_device = False
        device_current = False

        preview_player = AudioPlayer()
        preview_timer = QTimer(page)
        preview_timer.setInterval(100)
        playing_row = None
        row_controls = []
        linked_samples = {}
        empty_preview_slots = set()
        backup_wavs = {}
        settings = QSettings("VolcaStudio", "VolcaStudio")
        preview_controls = QWidget()
        preview_layout = QHBoxLayout(preview_controls)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        tempo = QSpinBox()
        tempo.setObjectName("patternTempo")
        tempo.setButtonSymbols(QSpinBox.NoButtons)
        tempo.setMinimumWidth(96)
        tempo.setAlignment(Qt.AlignCenter)
        tempo.setRange(30, 300)
        tempo.setSuffix(" BPM")
        tempo.setAccessibleName("Preview tempo")
        tempo.setToolTip("Local preview tempo. Volca pattern data does not include BPM.")
        choose_wavs = QPushButton("WAV backup…")
        fetch_wavs = QPushButton("Read missing samples")
        tempo.setFixedHeight(choose_wavs.sizeHint().height())
        preview_layout.addWidget(tempo)
        preview_layout.addWidget(choose_wavs)
        preview_layout.addWidget(fetch_wavs)
        preview_layout.addStretch()
        preview_controls.hide()
        layout.insertWidget(layout.indexOf(archive_actions), preview_controls)

        def tempo_key(row):
            slot, _, data = patterns[row]
            return f"pattern-preview-tempo/{slot}-{sha256(data).hexdigest()}"

        def update_preview_selection():
            row = listing.currentRow()
            tempo.setEnabled(row >= 0)
            fetch_wavs.setEnabled(row >= 0 and showing_device and device_current)
            with QSignalBlocker(tempo):
                tempo.setValue(settings.value(tempo_key(row), 120, type=int) if row >= 0 else 120)

        def save_tempo(value):
            row = listing.currentRow()
            if row >= 0:
                settings.setValue(tempo_key(row), value)

        def available_paths():
            paths = {slot: path for slot, path in backup_wavs.items() if path.is_file()}
            samples = {sample["id"]: Path(sample["path"]) for sample in self.library.samples}
            for slot, sample_id in linked_samples.items():
                path = samples.get(sample_id)
                if path is not None and path.is_file():
                    paths[slot] = path
            return paths

        def stop_preview():
            nonlocal playing_row
            preview_timer.stop()
            preview_player.stop()
            if playing_row is not None:
                button = row_controls[playing_row][3]
                button.setText("▶")
                button.setAccessibleName("Play pattern on Mac")
                playing_row = None

        def check_preview():
            if not preview_player.is_playing():
                stop_preview()

        def resolve_missing_wavs(missing):
            dialog = QMessageBox(self)
            dialog.setWindowTitle("Pattern preview")
            dialog.setIcon(QMessageBox.Information)
            dialog.setText("This pattern needs more samples")
            slots = ", ".join(f"{slot:03d}" for slot in sorted(missing))
            dialog.setInformativeText(f"Missing WAVs for Volca slots {slots}.")
            read = None
            if showing_device and device_current:
                read = dialog.addButton(tr("Read from Volca"), QMessageBox.AcceptRole)
            backup = dialog.addButton("Choose WAV backup…", QMessageBox.ActionRole)
            dialog.addButton(QMessageBox.Cancel)
            dialog.setDefaultButton(read or backup)
            dialog.exec()
            selected = dialog.clickedButton()
            if read is not None and selected == read:
                return fetch_missing_wavs()
            if selected == backup:
                return choose_backup_wavs()
            return False

        def play_pattern(row):
            nonlocal playing_row
            was_playing = playing_row == row
            stop_preview()
            listing.setCurrentRow(row)
            if was_playing:
                return
            try:
                pattern = patterns[row][2]
                required = required_slots(pattern)
                paths = available_paths()
                missing = required - paths.keys() - empty_preview_slots
                if missing:
                    if not resolve_missing_wavs(missing):
                        return
                    paths = available_paths()
                with tempfile.TemporaryDirectory(prefix="VolcaStudio-pattern-preview-") as folder:
                    path = Path(folder) / "pattern.wav"
                    render_pattern_preview(
                        pattern, paths, tempo.value(), path, empty_slots=empty_preview_slots,
                    )
                    self.slots_page.stop_playback()
                    self._stop_device_preview()
                    preview_player.play(path)
            except Exception as exc:
                QMessageBox.warning(self, "Could not preview pattern", str(exc))
                return
            playing_row = row
            button = row_controls[row][3]
            button.setText("■")
            button.setAccessibleName("Stop pattern preview")
            preview_timer.start()

        def choose_backup_wavs():
            folder = QFileDialog.getExistingDirectory(self, "Choose the WAV backup for these patterns")
            if not folder:
                return False
            try:
                rows = list_backup_wavs(Path(folder))
                paths = {slot: path for slot, _, path, _ in rows if path is not None}
                if not paths:
                    raise ValueError("This folder contains no usable Volca WAV files.")
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Could not open WAV backup", str(exc))
                return False
            stop_preview()
            backup_wavs.clear()
            backup_wavs.update(paths)
            choose_wavs.setToolTip(folder)
            return True

        def fetch_missing_wavs():
            row = listing.currentRow()
            if row < 0 or not (showing_device and device_current):
                return False
            try:
                missing = sorted(required_slots(patterns[row][2]) - available_paths().keys() - empty_preview_slots)
                if not missing:
                    QMessageBox.information(self, "Pattern preview", "All required samples are available.")
                    return False
                stop_preview()
                with tempfile.TemporaryDirectory(prefix="VolcaStudio-pattern-samples-") as folder:
                    def read_samples():
                        downloaded = []
                        empty = set()
                        for slot in missing:
                            path = Path(folder) / f"{slot:03d}.wav"
                            try:
                                self._backup_slot(slot, path)
                            except EmptySampleSlotError:
                                empty.add(slot)
                                continue
                            except Exception as exc:
                                return downloaded, empty, f"Slot {slot:03d}. {exc}"
                            downloaded.append((slot, path))
                        return downloaded, empty, ""

                    downloaded, empty, error = self._run_device_operation(
                        "Reading missing pattern samples…", read_samples,
                    )
                    empty_preview_slots.update(empty)
                    for slot, path in downloaded:
                        sample = self.library.find_identical_wav(path)
                        if sample is None:
                            prepared = path.with_name(f"{slot:03d}-playback.wav")
                            prepare_playback_wav(path, prepared)
                            sample = self.library.find_identical_wav(prepared)
                            if sample is None:
                                sample = self.library.add(path, prepared, name=f"Volca {slot:03d}")
                        linked_samples[slot] = sample["id"]
                    if error:
                        raise RuntimeError(error)
            except Exception as exc:
                QMessageBox.warning(self, "Could not read missing samples", str(exc))
                return False
            finally:
                self.slots_page.refresh()
            return True

        def invalidate_preview_samples():
            stop_preview()
            linked_samples.clear()
            empty_preview_slots.clear()
            if showing_device:
                backup_wavs.clear()
                choose_wavs.setToolTip("")
                clear_comparison()

        def set_pattern_label(row, slot, name, status=""):
            number, label, comparison, _ = row_controls[row]
            name = name or tr("Unnamed pattern")
            number.setText(f"{slot:02d}")
            label.setText(name)
            label.setToolTip(name)
            translated(comparison, status)
            comparison.setStyleSheet("color: #d7a3a6;" if status == "Different" else "")
            comparison.setVisible(bool(status))
            parts = (number.text(), name, status) if status else (number.text(), name)
            listing.item(row).setData(Qt.AccessibleTextRole, " · ".join(parts))

        preview_timer.timeout.connect(check_preview)
        tempo.valueChanged.connect(save_tempo)
        choose_wavs.clicked.connect(choose_backup_wavs)
        fetch_wavs.clicked.connect(fetch_missing_wavs)
        self._stop_pattern_preview = stop_preview
        self._invalidate_pattern_samples = invalidate_preview_samples

        def update_actions():
            row = listing.currentRow()
            update_preview_selection()
            export.setEnabled(row >= 0)
            compare.setEnabled(bool(patterns) and not showing_device and read_live.isEnabled())
            edit.setEnabled(row >= 0)
            editable = showing_device and device_current and row >= 0
            rename.setEnabled(editable)
            reset.setEnabled(editable)
            steps.setText(tr("Edit steps…") if editable else tr("View steps…"))
            current = live_patterns.get(patterns[row][0]) if row >= 0 else None
            restore.setEnabled(current is not None)

        self._update_pattern_actions = update_actions

        def show_comparison():
            matches = 0
            for row, (slot, name, data) in enumerate(patterns):
                match = data == live_patterns[slot][1]
                matches += match
                status = "Matches" if match else "Different"
                set_pattern_label(row, slot, name, status)
            archive_label.setText(f"Backup · {archive_name} · {matches} match · {len(patterns) - matches} differ")
            update_actions()

        def clear_comparison():
            nonlocal device_current
            device_current = False
            self._set_device_pattern_usage(None)
            live_patterns.clear()
            archive_label.setText("Last read · Read from Volca to update" if showing_device
                                  else f"Backup · {archive_name} · {len(patterns)} patterns")
            for row, (slot, name, _) in enumerate(patterns):
                set_pattern_label(row, slot, name)
            update_actions()

        self._invalidate_pattern_comparison = clear_comparison

        def update_pattern_connection(detected):
            if read_live.isEnabled() != detected:
                read_live.setEnabled(detected)
                backup.setEnabled(detected)
                update_actions()

        self._update_pattern_connection = update_pattern_connection
        listing.currentRowChanged.connect(update_actions)

        def resize_pattern_rows():
            padding = 3 if self.compact or _SHOW_PATTERN_PREVIEW else 5
            for row in range(listing.count()):
                item = listing.item(row)
                row_layout = listing.itemWidget(item).layout()
                row_layout.setContentsMargins(8, padding, 8, padding)
                item.setSizeHint(row_layout.sizeHint())

        self._resize_pattern_rows = resize_pattern_rows

        archive_path = None

        def show_patterns(items, source, from_device, *, path=None):
            nonlocal patterns, archive_name, showing_device, device_current, archive_path
            archive_path = path
            stop_preview()
            linked_samples.clear()
            empty_preview_slots.clear()
            backup_wavs.clear()
            choose_wavs.setToolTip("")
            patterns = items
            archive_name = source
            showing_device = device_current = from_device
            if from_device:
                self._set_device_pattern_usage(items)
            live_patterns.clear()
            archive_label.setText(f"{archive_name if from_device else 'Backup · ' + archive_name} · {len(patterns)} patterns")
            listing.clear()
            row_controls.clear()
            for row, (slot, name, _) in enumerate(patterns):
                item = QListWidgetItem()
                listing.addItem(item)
                widget = QWidget()
                widget.setObjectName("patternRow")
                row_layout = QHBoxLayout(widget)
                play = QPushButton("▶")
                play.setFixedSize(38, 24)
                play.setObjectName("patternPreview")
                play.setAccessibleName("Play pattern on Mac")
                play.setToolTip("Play one pass on Mac using local audio.")
                play.clicked.connect(lambda checked=False, index=row: play_pattern(index))
                row_layout.addWidget(play)
                play.setVisible(_SHOW_PATTERN_PREVIEW)
                number = QLabel()
                number.setObjectName("muted")
                number.setFixedWidth(28)
                number.setAttribute(Qt.WA_TransparentForMouseEvents)
                row_layout.addWidget(number)
                label = QLabel()
                label.setTextFormat(Qt.PlainText)
                label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                label.setAttribute(Qt.WA_TransparentForMouseEvents)
                row_layout.addWidget(label, 1)
                comparison = QLabel()
                comparison.setObjectName("muted")
                comparison.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                comparison.setAttribute(Qt.WA_TransparentForMouseEvents)
                row_layout.addWidget(comparison)
                row_controls.append((number, label, comparison, play))
                set_pattern_label(row, slot, name)
                listing.setItemWidget(item, widget)
            resize_pattern_rows()
            listing.setCurrentRow(-1)
            archive_label.show()
            view.setCurrentWidget(listing)
            archive_actions.show()
            preview_controls.setVisible(_SHOW_PATTERN_PREVIEW)
            compare.setVisible(not from_device)
            restore.setVisible(not from_device)
            edit.show()
            edit_separator.setVisible(from_device)
            rename.setVisible(from_device)
            reset.setVisible(from_device)
            update_actions()

        def load_patterns(path):
            try:
                items = load_pattern_backup(path)
            except (OSError, ValueError, RuntimeError) as exc:
                QMessageBox.warning(self, tr("Could not open pattern backup"), str(exc))
                return False
            show_patterns(items, Path(path).name, False, path=Path(path))
            return True

        self._open_pattern_archive = load_patterns

        def open_pattern_recovery(path):
            if load_patterns(path) and listing.count() == 1:
                listing.setCurrentRow(0)

        def read_device_patterns():
            try:
                items = self._run_device_operation("Reading patterns from Volca…", self._read_live_patterns)
            except Exception as exc:
                clear_comparison()
                QMessageBox.warning(self, tr("Could not read patterns"), str(exc))
                return
            show_patterns(items, "On Volca", True)

        def open_device_pattern(number):
            if not (showing_device and device_current):
                read_device_patterns()
            if showing_device and device_current:
                for row, (slot, _, _) in enumerate(patterns):
                    if slot == number:
                        listing.setCurrentRow(row)
                        self.switch_page(2)
                        listing.setFocus()
                        break

        self._open_device_pattern = open_device_pattern

        def rename_pattern():
            row = listing.currentRow()
            if row < 0 or not rename.isEnabled():
                return
            slot, old_name, previous = patterns[row]
            name = self._ask_device_name(f"Rename pattern {slot:02d} on Volca", old_name, 32)
            if name is None or name == old_name:
                return
            replacement = previous[:16] + name.encode("ascii").ljust(32, b"\0") + previous[48:]
            write_live_pattern(row, name, replacement)

        def reset_pattern():
            row = listing.currentRow()
            if row < 0 or not reset.isEnabled():
                return
            slot, _, previous = patterns[row]
            try:
                replacement = load_init_pattern()
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, tr("Could not reset pattern"), str(exc))
                return
            if previous == replacement:
                QMessageBox.information(self, tr("Reset pattern"), f"Pattern {slot:02d} is already initialized.")
                return
            if QMessageBox.question(
                self, "Reset pattern on Volca",
                f"Reset pattern {slot:02d} to Init Pattern? A recovery copy will be saved first.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) == QMessageBox.Yes:
                write_live_pattern(row, "Init Pattern", replacement)

        def open_steps():
            row = listing.currentRow()
            if row < 0:
                return
            slot, name, data = patterns[row]
            editable = showing_device and device_current
            try:
                sample_slots = None
                if editable:
                    try:
                        sample_slots = {number: label for number, label, duration in self._read_device_slots()
                                        if duration is not None}
                    except Exception as exc:
                        QMessageBox.warning(self, tr("Could not read Volca slots"), str(exc))
                elif archive_path is not None:
                    with ZipFile(archive_path) as archive:
                        if "manifest.json" in archive.namelist():
                            sample_slots = {
                                sample.slot: sample.header[0].split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
                                for sample in load_samples(archive_path)
                            }
                dialog = PatternStepsDialog(slot, name, data, editable, self, sample_slots=sample_slots)
            except (BadZipFile, KeyError, OSError, ValueError, RuntimeError) as exc:
                QMessageBox.warning(self, tr("Could not open pattern steps"), str(exc))
                return
            if dialog.exec() == QDialog.Accepted and editable and device_current and dialog.save.isEnabled():
                write_live_pattern(row, name, dialog.replacement())

        def write_live_pattern(row, name, replacement):
            slot, _, previous = patterns[row]
            stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            recovery = self.library.root / "Pattern Recovery" / f"Volca-Pattern-{slot:02d}-{stamp}-{uuid4().hex[:8]}.zip"
            try:
                self._run_device_operation(
                    f"Saving pattern {slot:02d}…", self._restore_pattern, slot, replacement, previous, recovery,
                )
            except Exception as exc:
                clear_comparison()
                self._show_recovery_result(tr("Could not update pattern"), str(exc), QMessageBox.Warning, recovery, open_pattern_recovery)
                return
            patterns[row] = (slot, name, replacement)
            self._set_device_pattern_usage(patterns)
            set_pattern_label(row, slot, name)
            self._show_recovery_result(
                tr("Pattern saved"), f"Volca verified pattern {slot:02d}: {name}.",
                QMessageBox.Information, recovery, open_pattern_recovery,
            )

        def open_patterns():
            path, _ = QFileDialog.getOpenFileName(self, "Open pattern backup", "", "ZIP archives (*.zip)")
            if path:
                load_patterns(path)

        def compare_patterns():
            try:
                live = self._run_device_operation("Reading patterns from Volca…", self._read_live_patterns)
            except Exception as exc:
                clear_comparison()
                QMessageBox.warning(self, tr("Could not compare patterns"), str(exc))
                return
            self._set_device_pattern_usage(live)
            live_patterns.update({slot: (name, data) for slot, name, data in live})
            show_comparison()

        def restore_selected():
            row = listing.currentRow()
            if row < 0 or not restore.isEnabled():
                return
            slot, name, data = patterns[row]
            current_name, expected = live_patterns[slot]
            def compare_audio(targets):
                if archive_path is None or not targets:
                    return {}
                with ZipFile(archive_path) as archive:
                    if "manifest.json" not in archive.namelist():
                        return {}
                command = (
                    "import json, sys; from app.device.sample_restore import compare_pattern_audio; "
                    "print(json.dumps(compare_pattern_audio(sys.argv[1], json.loads(sys.argv[2]))))"
                )
                result = self._run_device_operation(
                    tr("Checking pattern sample audio…"),
                    lambda: self._run_device_command(command, archive_path, json.dumps(targets), timeout=300),
                )
                return {int(source): status for source, status in json.loads(result).items()}

            def prepare_samples(destinations):
                try:
                    statuses = compare_audio(destinations)
                    needed = {source for source, status in statuses.items() if status != "matches"}
                    if not needed:
                        QMessageBox.information(self, tr("Prepare pattern samples…"),
                                                tr("Available snapshot samples already match."))
                        return {}
                    reserved = [target for source, target in destinations.items() if source not in needed]
                    fresh_slots, memory = self._read_device_slots(with_memory=True)
                    if memory is None:
                        raise ValueError(tr("Could not check free memory on Volca."))
                    used, total = memory
                    plan = plan_pattern_samples(archive_path, needed, reserved, fresh_slots, total - used)
                    lines = [f"{source:03d} → {target:03d}" for source, target in plan["writes"].items()]
                    detail = tr("Memory: {needed} KiB needed · {free} KiB free").format(
                        needed=plan["required"] // 1024, free=plan["free"] // 1024,
                    )
                    if plan["blocked"]:
                        detail += "\n" + tr("Could not place samples: {slots}").format(
                            slots=", ".join(f"{source:03d}" for source in plan["blocked"]),
                        )
                    if not lines or plan["required"] > plan["free"]:
                        QMessageBox.warning(self, tr("Prepare pattern samples…"), detail)
                        return {}
                    if QMessageBox.question(
                        self, tr("Prepare pattern samples…"),
                        tr("Write these samples to empty slots now? They will stay on Volca if you cancel the pattern restore.") + "\n\n" + "\n".join(lines) + "\n\n" + detail,
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                    ) != QMessageBox.Yes:
                        return {}
                    names = {sample.slot: sample.header[0].split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
                             for sample in load_samples(archive_path)}
                    command = (
                        "import json, sys; from app.device.sample_restore import write_pattern_samples; "
                        "print(json.dumps(write_pattern_samples(sys.argv[1], json.loads(sys.argv[2]))))"
                    )
                    if self.device_last_read.text():
                        self.device_last_read.setText("Last read · Refresh after changes")
                        self._set_device_slots_current(False)
                    result = json.loads(self._run_device_operation(
                        tr("Preparing pattern samples…"),
                        lambda: self._run_device_command(command, archive_path, json.dumps(plan), timeout=300),
                    ))
                except Exception as exc:
                    QMessageBox.warning(self, tr("Prepare pattern samples…"), str(exc))
                    return {}
                confirmed = {int(source): (target, names[int(source)] or tr("Unnamed sample"))
                             for source, target in result["verified"].items()}
                report = [tr("Verified sample slots: {slots}").format(
                    slots=", ".join(f"{target:03d}" for target, _ in confirmed.values()) or "—",
                )]
                pending = sorted(needed - confirmed.keys())
                if pending:
                    report.append(tr("Samples still to prepare: {slots}").format(
                        slots=", ".join(f"{source:03d}" for source in pending),
                    ))
                if result["error"]:
                    report.append(tr("Could not verify slot {slot:03d}. Refresh its status before retrying.").format(
                        slot=result["failed"],
                    ))
                    report.append(result["error"])
                report.append(tr("Pattern has not been written. Verified samples will stay on Volca."))
                show_result = QMessageBox.warning if result["error"] else QMessageBox.information
                show_result(self, tr("Prepare pattern samples…"), "\n\n".join(report))
                audio_status.update({source: "matches" for source, (target, _) in confirmed.items()
                                     if source == target})
                return confirmed

            try:
                device_slots = self._read_device_slots()
                slots = {number for number, _ in read_steps(data)[1]}
                audio_status = compare_audio({number: number for number in sorted(slots)})
                preview = PatternSampleSlotsDialog(
                    data, device_slots, self, audio_status=audio_status,
                    prepare_samples=prepare_samples if audio_status else None,
                )
            except Exception as exc:
                QMessageBox.warning(self, tr("Could not check pattern sample slots"), str(exc))
                return
            if preview.exec() != QDialog.Accepted:
                return
            data = preview.replacement()
            if data == expected:
                QMessageBox.information(self, tr("Restore pattern"), tr("This pattern already matches Volca."))
                return
            mapping = preview.mapping()
            try:
                audio_status.update(compare_audio(mapping))
            except Exception as exc:
                QMessageBox.warning(self, tr("Could not check pattern sample slots"), str(exc))
                return
            empty_slots = sorted(set(preview.empty_slots()) | {
                mapping.get(source, source) for source, status in audio_status.items() if status == "empty"
            })
            confirmation = QMessageBox(self)
            confirmation.setWindowTitle(tr("Restore pattern on Volca"))
            confirmation.setTextFormat(Qt.PlainText)
            confirmation.setText(
                f"Replace slot {slot:02d} on Volca?\n\n"
                f"On Volca: {current_name or tr('Unnamed pattern')}\n"
                f"From backup: {name or tr('Unnamed pattern')}\n\n"
                "The current pattern will be saved to a recovery ZIP before writing."
            )
            details = []
            for source in sorted(mapping.keys() | audio_status.keys()):
                destination = mapping.get(source, source)
                status = audio_status.get(source)
                label = tr(AUDIO_LABELS[status]) if status else ""
                details.append(f"{source:03d} → {destination:03d}" + (f" · {label}" if label else ""))
            if details:
                confirmation.setDetailedText("\n".join(details))
            notices = []
            if empty_slots:
                notices.append(tr("Empty destination slots: {slots}").format(
                    slots=", ".join(f"{number:03d}" for number in empty_slots),
                ))
            different = sorted({mapping.get(source, source) for source, status in audio_status.items()
                                if status == "different"})
            if different:
                notices.append(tr("Different audio in slots: {slots}").format(
                    slots=", ".join(f"{number:03d}" for number in different),
                ))
            confirmation.setInformativeText("\n".join(notices))
            confirm = confirmation.addButton(tr("Restore pattern"), QMessageBox.AcceptRole)
            cancel = confirmation.addButton(QMessageBox.Cancel)
            confirmation.setDefaultButton(cancel)
            confirmation.exec()
            if confirmation.clickedButton() != confirm:
                return
            stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            folder = self.library.root / "Pattern Recovery"
            recovery = folder / f"Volca-Pattern-{slot:02d}-{stamp}-{uuid4().hex[:8]}.zip"
            try:
                self._run_device_operation(
                    f"Restoring pattern {slot:02d}…", self._restore_pattern, slot, data, expected, recovery,
                )
            except Exception as exc:
                clear_comparison()
                self._show_recovery_result(tr("Could not verify restore"), str(exc), QMessageBox.Warning, recovery, open_pattern_recovery)
                return
            live_patterns[slot] = (name, data)
            self._set_device_pattern_usage([(slot, name, data) for slot, (name, data) in live_patterns.items()])
            show_comparison()
            self._show_recovery_result(tr("Pattern restored"), f"Verified slot {slot:02d} on Volca.",
                                       QMessageBox.Information, recovery, open_pattern_recovery)

        def export_pattern():
            row = listing.currentRow()
            if row < 0:
                return
            slot, _, data = patterns[row]
            filename = f"Volca-Pattern-{slot:02d}.vlcsplpattb"
            path, _ = QFileDialog.getSaveFileName(
                self, "Save pattern", filename, "Volca pattern (*.vlcsplpattb)",
            )
            if not path:
                return
            file = QSaveFile(path)
            if not file.open(QIODevice.WriteOnly) or file.write(data) != len(data) or not file.commit():
                QMessageBox.warning(self, tr("Could not save pattern"), file.errorString())
                return
            QMessageBox.information(self, tr("Pattern saved"), f"Saved pattern {slot:02d}.\n\n{path}")

        open_backup.clicked.connect(open_patterns)
        read_live.clicked.connect(read_device_patterns)
        export.clicked.connect(export_pattern)
        compare.clicked.connect(compare_patterns)
        restore.clicked.connect(restore_selected)
        steps.triggered.connect(open_steps)
        listing.itemDoubleClicked.connect(open_steps)
        rename.triggered.connect(rename_pattern)
        reset.triggered.connect(reset_pattern)
        return page

    def _show_recovery_result(self, title, message, icon, recovery, open_copy):
        result = QMessageBox(self)
        result.setWindowTitle(title)
        result.setTextFormat(Qt.PlainText)
        result.setIcon(icon)
        close = result.addButton(QMessageBox.Close)
        result.setDefaultButton(close)
        open_recovery = None
        if recovery.is_file():
            result.setDetailedText(f"Recovery ZIP\n{recovery}")
            open_recovery = result.addButton("Open recovery copy", QMessageBox.ActionRole)
        result.setText(message)
        result.exec()
        if open_recovery is not None and result.clickedButton() == open_recovery:
            open_copy(recovery)

    def backup_device_patterns(self):
        name = datetime.now().strftime("Volca-Patterns-%Y-%m-%d-%H%M%S.zip")
        path, _ = QFileDialog.getSaveFileName(self, "Back up Volca patterns", name, "ZIP archives (*.zip)")
        if not path:
            return
        destination = Path(path)
        if destination.suffix.lower() != ".zip":
            destination = Path(f"{path}.zip")
            if destination.exists() and QMessageBox.question(
                self, tr("Replace backup?"), f"Replace {destination.name}?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                return
        try:
            self._run_device_operation("Reading 16 stored patterns…", self._save_pattern_backup, destination)
        except Exception as exc:
            QMessageBox.warning(self, tr("Could not back up patterns"), str(exc))
        else:
            QMessageBox.information(self, tr("Pattern backup complete"), f"Saved all 16 patterns.\n\n{destination}")

    def _read_live_patterns(self):
        with tempfile.TemporaryDirectory(prefix="Volca-compare-") as folder:
            path = Path(folder) / "patterns.zip"
            self._save_pattern_backup(path)
            return load_pattern_backup(path)

    def _run_device_operation(self, label, operation, *args, parent=None):
        progress = QProgressDialog(label, "", 0, 0, parent or self)
        progress.setWindowTitle("Volca Sample 2")
        progress.setCancelButton(None)
        progress.setWindowFlag(Qt.WindowCloseButtonHint, False)
        progress.setWindowModality(Qt.ApplicationModal)
        progress.show()
        self.timer.stop()
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                return wait_for_device(executor.submit(operation, *args))
        finally:
            self.timer.start()
            progress.close()

    @staticmethod
    def _save_pattern_backup(destination):
        command = (
            "import sys; from app.device.pattern_backup import save_pattern_backup; "
            "save_pattern_backup(sys.argv[1])"
        )
        MainWindow._run_device_command(command, destination)

    @staticmethod
    def _restore_pattern(slot, data, expected, recovery):
        command = (
            "import sys; from pathlib import Path; "
            "from app.device.pattern_backup import restore_pattern_with_backup; "
            "restore_pattern_with_backup(int(sys.argv[1]), Path(sys.argv[2]).read_bytes(), "
            "Path(sys.argv[3]).read_bytes(), sys.argv[4])"
        )
        with tempfile.TemporaryDirectory(prefix="Volca-restore-") as folder:
            new = Path(folder) / "replacement"
            old = Path(folder) / "expected"
            new.write_bytes(data)
            old.write_bytes(expected)
            MainWindow._run_device_command(command, slot, new, old, recovery)

    @staticmethod
    def _run_device_command(command, *args, timeout=45):
        try:
            result = subprocess.run(
                device_worker_argv(command, *(str(arg) for arg in args)),
                cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Volca did not finish the operation within {timeout} seconds.") from exc
        if result.returncode:
            lines = result.stderr.strip().splitlines()
            raise RuntimeError(lines[-1] if lines else "The device operation closed unexpectedly.")
        return result.stdout

    def _read_device_slots(self, parent=None, *, with_memory=False):
        reader = "slot_overview" if with_memory else "list_slots"
        command = (
            f"import json; from app.device.volca_sample2 import {reader}; "
            f"print(json.dumps({reader}()))"
        )
        return json.loads(self._run_device_operation(
            "Reading sample slots from Volca…",
            lambda: self._run_device_command(command, timeout=90),
            parent=parent,
        ))

    @staticmethod
    def _backup_slot(slot, destination):
        command = (
            "import sys; "
            "from app.device.volca_sample2 import EmptySampleSlotError, InvalidSlotReplyError, save_slot_wav\n"
            "try:\n"
            "    save_slot_wav(int(sys.argv[1]), sys.argv[2])\n"
            "except InvalidSlotReplyError as exc:\n"
            "    print(exc, file=sys.stderr)\n"
            "    sys.exit(2)\n"
            "except EmptySampleSlotError as exc:\n"
            "    print(exc, file=sys.stderr)\n"
            "    sys.exit(3)\n"
        )
        with tempfile.TemporaryDirectory(prefix=".volca-wav-", dir=destination.parent) as folder:
            target = Path(folder) / "sample.wav"
            try:
                result = subprocess.run(
                    device_worker_argv(command, str(slot), str(target)),
                    cwd=Path(__file__).resolve().parents[2],
                    capture_output=True, text=True, timeout=30,
                )
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError(
                    f"Slot {slot:03d} exceeded the 30-second read limit."
                ) from exc
            message = result.stderr.strip().splitlines()
            detail = message[-1] if message else "WAV reader exited unexpectedly."
            if result.returncode == 2:
                raise InvalidSlotReplyError(detail)
            if result.returncode == 3:
                raise EmptySampleSlotError(detail)
            if result.returncode:
                raise RuntimeError(detail)
            target.replace(destination)

    def backup_device_samples(self, parent, slots=(), resume=False, include_patterns=False):
        occupied = [number for number, _, duration in slots if duration is not None]
        if not occupied and not (resume or include_patterns):
            QMessageBox.information(parent, tr("Volca backup"), tr("No occupied slots in the current list."))
            return
        folder = QFileDialog.getExistingDirectory(
            parent, "Choose an existing Volca backup folder" if resume else
            "Choose where to save samples and patterns" if include_patterns else
            f"Choose where to back up {len(occupied)} Volca slots",
        )
        if not folder:
            return
        try:
            destination = Path(folder)
            if resume and not (destination / "Volca backup.json").is_file():
                raise ValueError("Choose a backup folder that contains Volca backup.json.")
            if resume or include_patterns:
                previous = {number for number, _, duration in slots if duration is not None}
                had_listing = bool(slots)
                slots, memory = self._read_device_slots(parent, with_memory=True)
                current = {number for number, _, duration in slots if duration is not None}
                self._apply_device_slot_overview(slots, memory)
                if had_listing and len(previous) != len(current):
                    self.device_operation_status.setText(
                        tr("Backup scan updated occupied slots · {before} → {after}").format(
                            before=len(previous), after=len(current),
                        )
                    )
            if not resume:
                label = "Volca-Backup" if include_patterns else "Volca-WAV"
                prefix = datetime.now().strftime(f"{label}-%Y-%m-%d-%H%M%S-")
                destination = Path(tempfile.mkdtemp(prefix=prefix, dir=folder))
            backup = WavBackup(destination, slots, include_patterns=include_patterns)
            with backup.open(resume):
                if backup.include_patterns:
                    try:
                        self._ensure_pattern_backup(destination)
                    except Exception as exc:
                        QMessageBox.warning(
                            parent, "Backup paused",
                            f"Could not save patterns.\n{exc}\n\nResume backup from:\n{destination}",
                        )
                        return
                self._run_device_backup(parent, backup)
        except Exception as exc:
            QMessageBox.warning(parent, tr("Could not open backup"), str(exc))

    def _ensure_pattern_backup(self, folder):
        archive = folder / "Volca-Patterns.zip"
        try:
            complete = len(load_pattern_backup(archive)) == 16
        except (OSError, ValueError, RuntimeError):
            complete = False
        if not complete:
            self._run_device_operation("Saving 16 stored patterns…", self._save_pattern_backup, archive)

    def _run_device_backup(self, parent, backup):
        destination = backup.folder
        occupied = backup.pending()
        total = len(backup.slots)
        patterns_summary = "\nVerified 16 patterns in Volca-Patterns.zip." if backup.include_patterns else ""
        if not occupied:
            QMessageBox.information(
                parent, tr("Backup complete"), f"Verified all {total} WAV files.{patterns_summary}\n\n{destination}",
            )
            return

        progress = QProgressDialog("Preparing backup…", "Stop after current slot", 0, len(occupied), parent)
        progress.setWindowTitle(tr("Volca WAV backup"))
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoReset(False)
        saved = total - len(occupied)
        error = None
        skipped = []
        progress.setValue(0)
        progress.setAutoClose(False)
        stopping = False

        def request_stop():
            nonlocal stopping
            stopping = True
            progress.setLabelText("Stopping after current slot…")
            progress.setCancelButton(None)
            progress.show()

        progress.canceled.connect(request_stop)
        progress.show()
        self.timer.stop()
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                for position, slot in enumerate(occupied, start=1):
                    if stopping:
                        break
                    progress.setLabelText(f"Reading slot {slot:03d} · {position}/{len(occupied)}")
                    pending = executor.submit(self._backup_slot, slot, backup.path(slot))
                    try:
                        wait_for_device(pending)
                        backup.record(slot)
                    except InvalidSlotReplyError as exc:
                        skipped.append((slot, str(exc)))
                    except Exception as exc:
                        error = f"Stopped at slot {slot:03d}. {exc}"
                        break
                    else:
                        saved += 1
                    if not stopping:
                        progress.setValue(position)
        finally:
            self.timer.start()
            progress.canceled.disconnect(request_stop)
            progress.close()

        result = f"Verified {saved} of {total} WAV files.{patterns_summary}\n\n{destination}"
        if skipped:
            numbers = ", ".join(f"{slot:03d}" for slot, _ in skipped)
            result += f"\n\nSkipped slots: {numbers}"
            try:
                report = destination / "Skipped slots.txt"
                report.write_text("\n".join(detail for _, detail in skipped) + "\n", encoding="utf-8")
            except OSError as exc:
                result += f"\nCould not save the report: {exc}"
        if error:
            QMessageBox.warning(parent, tr("Backup stopped"), f"{result}\n\n{error}")
        elif skipped:
            title = tr("Backup cancelled") if stopping else "Backup completed with skipped slots"
            QMessageBox.warning(parent, title, result)
        else:
            title = tr("Backup complete") if saved == total else tr("Backup cancelled")
            QMessageBox.information(parent, title, result)

    def _prepare_device_audio(self, slot, path):
        source = path.with_name("device.wav")
        self._backup_slot(slot, source)
        prepare_playback_wav(source, path)
        return source

    def save_device_sample(self, parent, slot):
        path, _ = QFileDialog.getSaveFileName(
            parent, "Save sample as WAV", f"Volca-{slot:03d}.wav", "WAV files (*.wav)"
        )
        if not path:
            return
        try:
            self._run_device_operation(
                f"Reading sample {slot:03d} from Volca…", self._backup_slot, slot, Path(path), parent=parent,
            )
        except Exception as exc:
            QMessageBox.warning(parent, tr("Could not save sample"), str(exc))
        else:
            QMessageBox.information(parent, tr("Sample saved"), f"Slot {slot:03d} saved to WAV.")

    def _ask_device_name(self, title, current, limit):
        value, accepted = QInputDialog.getText(
            self, title, f"Name (up to {limit} printable ASCII characters):", text=current,
        )
        if not accepted:
            return None
        name = value.strip()
        if not 1 <= len(name) <= limit or any(not 32 <= ord(char) <= 126 for char in name):
            QMessageBox.warning(self, tr("Invalid name"), f"Use 1–{limit} printable ASCII characters.")
            return None
        return name

    def _edit_device_sample(self, slot, old_name, duration, name):
        folder = self.library.root / "Device Recovery"
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        recovery = folder / f"Volca-Sample-{slot:03d}-{stamp}-{uuid4().hex[:8]}.zip"
        command = (
            "import json, sys; from app.device.sample_restore import edit_device_sample; "
            "edit_device_sample(int(sys.argv[1]), sys.argv[2], int(sys.argv[3]), json.loads(sys.argv[4]), sys.argv[5])"
        )
        try:
            folder.mkdir(exist_ok=True)
            self._run_device_operation(
                f"Backing up and updating sample {slot:03d}…",
                lambda: self._run_device_command(
                    command, slot, old_name, round(duration * 31250), json.dumps(name), recovery, timeout=90,
                ),
            )
        except (OSError, ValueError, RuntimeError) as exc:
            self._mark_device_slots_changed()
            self._show_recovery_result(
                "Could not update sample", str(exc), QMessageBox.Warning, recovery, self.preview_sample_restore,
            )
            return None
        return recovery

    def save_device_snapshot(self):
        name = datetime.now().strftime("Volca-Snapshot-%Y-%m-%d-%H%M%S.zip")
        path, _ = QFileDialog.getSaveFileName(self, "Save Volca device snapshot", name, "ZIP archives (*.zip)")
        if not path:
            return
        command = (
            "import json, sys; from app.device.sample_restore import save_snapshot; "
            "print(json.dumps(save_snapshot(sys.argv[1])))"
        )
        try:
            output = self._run_device_operation(
                "Saving samples and patterns from Volca…",
                lambda: self._run_device_command(command, path, timeout=900),
            )
            samples, patterns = json.loads(output)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, tr("Could not save device snapshot"), str(exc))
            return
        QMessageBox.information(self, tr("Device snapshot saved"), f"Saved {samples} samples and {patterns} patterns.\n\n{path}")

    def clear_volca(self):
        if QMessageBox.question(
            self, tr("Back up and clear Volca"),
            "Clear all sample slots and reset all 16 patterns on Volca?\n\n"
            "VolcaStudio will save and verify a recovery snapshot before clearing.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        folder = self.library.root / "Device Recovery"
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        recovery = folder / f"Volca-before-clear-{stamp}-{uuid4().hex[:8]}.zip"
        command = (
            "import json, sys; from app.device.sample_restore import clear_device; "
            "print(json.dumps(clear_device(sys.argv[1])))"
        )
        try:
            folder.mkdir(exist_ok=True)
            self._mark_device_slots_changed()
            self._invalidate_pattern_comparison()
            output = self._run_device_operation(
                "Backing up, clearing and verifying Volca…",
                lambda: self._run_device_command(command, recovery, timeout=1800),
            )
            samples, patterns = json.loads(output)
        except (OSError, ValueError, RuntimeError) as exc:
            detail = f"\n\nRecovery snapshot:\n{recovery}" if recovery.is_file() else ""
            QMessageBox.warning(self, tr("Could not complete Volca clear"), f"{exc}{detail}")
            return
        QMessageBox.information(
            self, tr("Volca cleared"),
            f"Cleared {samples} samples and verified {patterns} initial patterns.\n\n"
            f"Recovery snapshot:\n{recovery}",
        )

    def preview_sample_restore(self, path=None, *, preselected=()):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Open Volca device snapshot", "", "ZIP archives (*.zip)")
        if not path:
            return
        try:
            archive = inspect_snapshot(path)
        except (BadZipFile, KeyError, OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, tr("Could not open device snapshot"), tr(str(exc)))
            return

        plan = None
        if self.device_read_button.isEnabled() and archive["slots"]:
            command = (
                "import json, sys; from app.device.sample_restore import preview_snapshot; "
                "print(json.dumps(preview_snapshot(sys.argv[1])))"
            )
            try:
                output = self._run_device_operation(
                    "Checking sample slots and free memory…",
                    lambda: self._run_device_command(command, path, timeout=180),
                )
                plan = json.loads(output)
            except (OSError, ValueError, RuntimeError) as exc:
                QMessageBox.warning(self, tr("Could not compare with Volca"), str(exc))

        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Volca · Sample restore preview"))
        dialog.resize(670, 520)
        layout = QVBoxLayout(dialog)
        rows = plan["slots"] if plan is not None else archive["slots"]
        free = sum(row["status"] == "free" for row in rows)
        conflicts = sum(row["status"] == "conflict" for row in rows)
        too_long = sum(row["status"] == "too_long" for row in rows)
        matches = sum(row["status"] == "matches" for row in rows)
        heading = QLabel(tr("Restore samples") if plan is not None else tr("Device snapshot"))
        heading.setObjectName("section")
        layout.addWidget(heading)
        snapshot_summary = QLabel(tr("{samples} samples · {patterns} patterns in this snapshot").format(
            samples=len(rows), patterns=archive["patterns"],
        ))
        snapshot_summary.setObjectName("muted")
        layout.addWidget(snapshot_summary)
        if plan is None:
            note = ("Sample slots and free memory are checked on Volca when you open this preview with USB connected."
                    if rows else "This snapshot contains no samples.")
            offline_note = QLabel(note)
            offline_note.setWordWrap(True)
            layout.addWidget(offline_note)
        else:
            summary = tr("{ready} ready · {matches} already match · {conflicts} conflicts").format(
                ready=free, matches=matches, conflicts=conflicts,
            )
            if too_long:
                summary += f" · {too_long} above transfer limit"
            layout.addWidget(QLabel(summary))
        search = QLineEdit()
        translated(search, "Search by slot or sample name", "setPlaceholderText")
        search.setClearButtonEnabled(True)
        ready_only = QComboBox()
        ready_only.addItems([tr("All slots"), tr("Ready to restore")])
        ready_only.setVisible(plan is not None)
        filters = QHBoxLayout()
        filters.addWidget(search, 1)
        filters.addWidget(ready_only)
        layout.addLayout(filters)
        labels = {
            "archived": tr("In snapshot"), "free": tr("Ready · empty slot"), "matches": tr("Already matches"),
            "conflict": tr("Occupied · different sample"), "too_long": tr("Above transfer limit"),
        }
        listing = TransferPreviewTable([
            (row["slot"], row["name"] or tr("Unnamed sample"), labels[row["status"]])
            for row in rows
        ])
        layout.addWidget(listing, 1)
        checkable = {}
        for index, row in enumerate(rows):
            if row["status"] == "free":
                item = listing.item(index, 0)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if row["slot"] in preselected else Qt.Unchecked)
                checkable[row["slot"]] = (item, row["bytes"])

        close = translated(QPushButton(), "Close")
        close.clicked.connect(dialog.reject)
        restore = QPushButton(tr("Restore selected…"))
        restore.setObjectName("primaryAction")
        restore.clicked.connect(dialog.accept)
        memory = QLabel()
        memory.setObjectName("transferMemory")
        memory.setWordWrap(True)
        select_visible = translated(QPushButton(), "Select visible")
        clear_selection = translated(QPushButton(), "Clear selection")
        selection_bar = QWidget()
        selection_layout = QHBoxLayout(selection_bar)
        selection_layout.setContentsMargins(0, 0, 0, 0)
        selection_layout.addWidget(memory, 1)
        selection_layout.addWidget(select_visible)
        selection_layout.addWidget(clear_selection)
        selection_bar.setVisible(plan is not None)
        layout.addWidget(selection_bar)

        def selected_slots():
            return [slot for slot, (item, _) in checkable.items() if item.checkState() == Qt.Checked]

        def update_selection():
            selected = selected_slots()
            required = sum(checkable[slot][1] for slot in selected)
            available = plan["free"] if plan is not None else 0
            fits = required <= available
            hidden = sum(listing.isRowHidden(checkable[slot][0].row()) for slot in selected)
            selection_text = f"{len(selected)} selected"
            if hidden:
                selection_text += f" ({hidden} hidden by filters)"
            note = f"{selection_text} · {required // 1024} KiB needed · {available // 1024} KiB free"
            memory.setText(note if fits else note + " · Not enough free memory")
            set_warning(memory, not fits)
            restore.setEnabled(bool(selected) and fits)
            restore.setText(tr("Restore {count}…").format(count=len(selected)) if selected else tr("Restore selected…"))
            clear_selection.setEnabled(bool(selected))
            select_visible.setEnabled(any(
                not listing.isRowHidden(item.row()) and item.checkState() == Qt.Unchecked
                for item, _ in checkable.values()
            ))

        def filter_slots():
            query = search.text().strip().casefold()
            for index, row in enumerate(rows):
                matches_query = query in f"{row['slot']:03d} {row['name']}".casefold()
                show = matches_query and (not ready_only.currentIndex() or row["status"] == "free")
                listing.setRowHidden(index, not show)
            update_selection()

        def set_selection(checked):
            with QSignalBlocker(listing):
                for item, _ in checkable.values():
                    if not checked or not listing.isRowHidden(item.row()):
                        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            update_selection()

        search.textChanged.connect(filter_slots)
        ready_only.currentIndexChanged.connect(filter_slots)
        listing.itemChanged.connect(update_selection)
        select_visible.clicked.connect(lambda: set_selection(True))
        clear_selection.clicked.connect(lambda: set_selection(False))
        select_visible.setAutoDefault(False)
        clear_selection.setAutoDefault(False)
        update_selection()
        buttons = QHBoxLayout()
        open_patterns = translated(QPushButton(), "View patterns…")
        open_patterns.setVisible(archive["patterns"] > 0)
        open_patterns.setAutoDefault(False)
        open_patterns.clicked.connect(lambda: dialog.done(2))
        open_samples = translated(QPushButton(), "Browse samples…")
        open_samples.setVisible(bool(rows))
        open_samples.setAutoDefault(False)
        open_samples.clicked.connect(lambda: dialog.done(3))
        buttons.addWidget(open_samples)
        buttons.addWidget(open_patterns)
        buttons.addStretch()
        restore.setVisible(plan is not None)
        for button in (close, restore):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        result = dialog.exec()
        if result == 3:
            self._browse_snapshot_samples(path)
        if result == 2 and self._open_pattern_archive(path):
            self.switch_page(2)
        if result != QDialog.Accepted or not restore.isEnabled():
            return
        selected = selected_slots()
        if QMessageBox.question(
            self, tr("Restore samples to Volca"),
            f"Write {len(selected)} selected samples to their original empty slots on Volca?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        expected = {"fingerprint": plan["fingerprint"], "slots": selected}
        self._restore_samples(path, expected)

    def _restore_samples(self, path, expected):
        self._mark_device_slots_changed()
        total = len(expected["slots"])
        command = (
            "import json, sys; from pathlib import Path; "
            "from app.device.sample_restore import restore_snapshot; "
            "with_status = Path(sys.argv[3]); stop = Path(sys.argv[4]); "
            "count = restore_snapshot(sys.argv[1], apply=True, expected=json.loads(sys.argv[2]), "
            "report=lambda message: with_status.write_text(message, encoding='utf-8'), should_stop=stop.exists); "
            "print(count)"
        )
        with tempfile.TemporaryDirectory(prefix="Volca-sample-restore-") as folder:
            status = Path(folder) / "progress.txt"
            stop = Path(folder) / "stop"
            progress = QProgressDialog("Checking slots again…", tr("Stop after current sample"), 0, total, self)
            progress.setWindowTitle(tr("Volca sample restore"))
            progress.setWindowModality(Qt.ApplicationModal)
            progress.setAutoClose(False)
            progress.setAutoReset(False)
            progress.setValue(0)
            verified = 0
            unverified = None

            def request_stop():
                stop.touch()
                progress.setCancelButton(None)
                progress.setLabelText(tr("Stopping after the current sample…"))
                progress.show()

            def update_progress():
                nonlocal verified, unverified
                try:
                    line = status.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    return
                state, _, rest = line.partition(" ")
                position = rest.split("/", 1)[0]
                if state in ("Writing", "Verified") and position.isdecimal():
                    index = int(position) - 1
                    if 0 <= index < total:
                        verified = index + (state == "Verified")
                        unverified = expected["slots"][index] if state == "Writing" else None
                        progress.setValue(verified)
                if not stop.exists() and line:
                    progress.setLabelText(line)

            progress.canceled.connect(request_stop)
            poll = QTimer(progress)
            poll.timeout.connect(update_progress)
            poll.start(250)
            progress.show()
            self.timer.stop()
            error = None
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending = executor.submit(
                        self._run_device_command, command, path, json.dumps(expected), status, stop, timeout=900,
                    )
                    verified = int(wait_for_device(pending).strip())
            except Exception as exc:
                update_progress()
                error = str(exc)
            finally:
                poll.stop()
                self.timer.start()
                progress.canceled.disconnect(request_stop)
                progress.close()
        if show_sample_result(self, self.device_operation_status, expected["slots"], verified,
                              error, unverified if error else None, paused_title="Restore paused",
                              failed_title="Restore stopped"):
            remaining = expected["slots"][verified:]
            QTimer.singleShot(0, self, lambda: self.preview_sample_restore(path, preselected=remaining))

    def _browse_snapshot_samples(self, path):
        with tempfile.TemporaryDirectory(prefix="VolcaStudio-snapshot-wavs-") as folder:
            try:
                rows = extract_snapshot_wavs(path, Path(folder))
            except (BadZipFile, KeyError, OSError, ValueError, RuntimeError) as exc:
                QMessageBox.warning(self, tr("Could not open snapshot samples"), str(exc))
                return
            self._show_sample_backup(rows, Path(path).name, export_wavs=True)

    def open_sample_backup(self):
        folder = QFileDialog.getExistingDirectory(self, "Open a Volca sample backup")
        if not folder:
            return
        try:
            rows = list_backup_wavs(Path(folder))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, tr("Could not open sample backup"), str(exc))
            return
        if not rows:
            QMessageBox.information(self, tr("Sample backup"), tr("No Volca sample files found in this folder."))
            return

        self._show_sample_backup(rows, Path(folder).name)

    def _show_sample_backup(self, rows, source_name, *, export_wavs=False):
        self.slots_page.stop_playback()
        self._stop_device_preview()
        player = AudioPlayer()
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Volca sample backup"))
        dialog.resize(560, 520)
        layout = QVBoxLayout(dialog)
        available = sum(path is not None for _, _, path, _ in rows)
        summary = QLabel(f"{source_name} · {available} of {len(rows)} WAV available")
        summary.setTextFormat(Qt.PlainText)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        search = QLineEdit()
        translated(search, "Search by slot number or sample name", "setPlaceholderText")
        search.setClearButtonEnabled(True)
        availability = QComboBox()
        availability.addItems([tr("All slots"), tr("Available WAVs"), tr("Unavailable WAVs")])
        filters = QHBoxLayout()
        filters.addWidget(search, 1)
        filters.addWidget(availability)
        layout.addLayout(filters)
        listing = QListWidget()
        listing.setSelectionMode(QAbstractItemView.ExtendedSelection)
        imported = set()
        for slot, name, path, detail in rows:
            item = QListWidgetItem(f"{slot:03d}   {name or tr('Unnamed sample')}   ·   {detail}", listing)
            if path is None:
                item.setFlags(Qt.NoItemFlags)
        layout.addWidget(listing)

        def play_selected():
            row = listing.currentRow()
            if row >= 0 and not listing.item(row).isHidden() and rows[row][2] is not None:
                try:
                    player.play(str(rows[row][2]))
                except Exception as exc:
                    QMessageBox.warning(dialog, tr("Could not play sample"), str(exc))

        buttons = QHBoxLayout()
        play = translated(QPushButton(), "Play")
        play.setEnabled(False)
        play.clicked.connect(play_selected)
        listing.itemDoubleClicked.connect(play_selected)
        add = translated(QPushButton(), "Add to library")
        add.setEnabled(False)
        export = translated(QPushButton(), "Export WAV…")
        export.setVisible(export_wavs)

        def selected_rows():
            return sorted(
                row for row in (listing.row(item) for item in listing.selectedItems())
                if not listing.item(row).isHidden() and rows[row][2] is not None
            )

        def update_actions():
            row = listing.currentRow()
            available = row >= 0 and not listing.item(row).isHidden() and rows[row][2] is not None
            play.setEnabled(available)
            selected = selected_rows()
            export.setEnabled(bool(selected))
            count = len(set(selected) - imported)
            add.setEnabled(count > 0)
            add.setText(f"Add {count} to library" if count > 1 else tr("Add to library"))

        def filter_samples():
            query = search.text().strip().casefold()
            mode = availability.currentIndex()
            with QSignalBlocker(listing):
                for row, (slot, name, path, _) in enumerate(rows):
                    matches_number = query.isdecimal() and query.lstrip("0") == str(slot).lstrip("0")
                    item = listing.item(row)
                    matches_search = not query or matches_number or query in name.casefold()
                    matches_availability = mode == 0 or (mode == 1) == (path is not None)
                    hidden = not (matches_search and matches_availability)
                    item.setHidden(hidden)
                    if hidden:
                        item.setSelected(False)
            update_actions()

        def add_selected():
            errors = []
            added = False
            for row in selected_rows():
                if row in imported:
                    continue
                slot, name, path, _ = rows[row]
                name = name or path.name
                try:
                    existing = self.library.find_identical_wav(path, None if export_wavs else name)
                    if existing is None:
                        self.library.add(path, path, name=name)
                        added = True
                except Exception as exc:
                    errors.append(f"{slot:03d} · {name or path.stem}: {exc}")
                    continue
                imported.add(row)
                item = listing.item(row)
                status = tr("Already in library") if existing is not None else tr("Added to library")
                item.setText(f"{item.text()}   ·   {status}")
            update_actions()
            if added:
                self.slots_page.refresh()
            if errors:
                QMessageBox.warning(dialog, tr("Could not add some samples"), "\n".join(errors))

        def export_selected():
            selected = selected_rows()
            if not selected:
                return
            parent = QFileDialog.getExistingDirectory(dialog, "Choose where to export WAVs")
            if not parent:
                return
            destination = None
            try:
                destination = Path(tempfile.mkdtemp(prefix="Volca-WAV-", dir=parent))
                for row in selected:
                    path = rows[row][2]
                    shutil.copy2(path, destination / path.name)
            except OSError as exc:
                if destination is not None:
                    shutil.rmtree(destination, ignore_errors=True)
                QMessageBox.warning(dialog, tr("Could not export WAVs"), str(exc))
                return
            QMessageBox.information(dialog, tr("WAVs exported"), f"Saved {len(selected)} WAV files.\n\n{destination}")

        export.clicked.connect(export_selected)
        listing.currentRowChanged.connect(update_actions)
        listing.itemSelectionChanged.connect(update_actions)
        search.textChanged.connect(filter_samples)
        availability.currentIndexChanged.connect(filter_samples)
        add.clicked.connect(add_selected)
        stop = translated(QPushButton(), "Stop")
        stop.clicked.connect(player.stop)
        close = translated(QPushButton(), "Close")
        close.clicked.connect(dialog.accept)
        for button in (play, stop, export, add, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        update_actions()
        try:
            dialog.exec()
        finally:
            player.stop()

    def _set_device_pattern_usage(self, patterns):
        usage = None
        if patterns is not None:
            usage = {}
            try:
                for number, _, data in sorted(patterns):
                    _, parts = read_steps(data)
                    for slot in {slot for slot, steps in parts if steps and slot < 200}:
                        usage.setdefault(slot, []).append(number)
            except ValueError:
                usage = None
        self._device_pattern_usage = usage
        self._refresh_device_pattern_usage()

    def _build_device_page(self):
        layout = QVBoxLayout(self.device_page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        volca_panel = self.volca_panel = VolcaPanel()
        volca_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout.addWidget(volca_panel)

        controls = QWidget()
        controls.setObjectName("deviceControls")
        controls.setFixedWidth(228)
        tools = QVBoxLayout(controls)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(6)
        tools.addStretch()
        refresh = self.device_read_button = translated(QPushButton(), "Read slots")
        refresh.setObjectName("primaryAction")
        refresh.setEnabled(False)
        tools.addWidget(refresh)
        connection_notice = translated(QLabel(), VOLCA_NO_REPLY)
        connection_notice.setObjectName("muted")
        connection_notice.setWordWrap(True)
        connection_notice.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        connection_notice.hide()
        tools.addWidget(connection_notice)

        secondary = QHBoxLayout()
        secondary.setSpacing(6)
        backups = translated(QPushButton(), "Backups")
        backups.setProperty("menuButton", True)
        backup_menu = QMenu(backups)
        backups.setMenu(backup_menu)
        secondary.addWidget(backups)
        diagnostics = translated(QPushButton(), "Diagnostics…")
        diagnostics.setToolTip("Connection and MIDI diagnostics")
        self._build_connection_dialog()
        diagnostics.clicked.connect(self.connection_dialog.exec)
        secondary.addWidget(diagnostics)
        tools.addLayout(secondary)
        tools.addStretch()
        volca_panel.layout().addWidget(controls)

        slots = []
        backup_menu.addAction(tr("Export WAVs and patterns to a folder…"),
                              lambda: self.backup_device_samples(self, slots, include_patterns=True))
        backup_slots = backup_menu.addAction(tr("Back up occupied slots…"),
                                             lambda: self.backup_device_samples(self, slots))
        backup_menu.addAction(tr("Resume backup…"), lambda: self.backup_device_samples(self, slots, resume=True))
        translated_action(backup_menu, "Open sample backup…", self.open_sample_backup)
        backup_menu.addSeparator()
        translated_action(backup_menu, "Save full device snapshot (ZIP)…", self.save_device_snapshot)
        translated_action(backup_menu, "Preview restore from snapshot (ZIP)…", lambda: self.preview_sample_restore())
        backup_menu.addSeparator()
        self.device_clear_action = backup_menu.addAction(tr("Back up and clear Volca…"), self.clear_volca)
        self.device_clear_action.setEnabled(False)

        section = QHBoxLayout()
        heading = translated(QLabel(), "Sample slots")
        heading.setObjectName("section")
        section.addWidget(heading)
        last_read = self.device_last_read = QLabel()
        last_read.setObjectName("muted")
        map_button = translated(QPushButton(), "Slot map")
        map_button.setObjectName("deviceMapToggle")
        map_button.setCheckable(True)
        map_button.hide()
        section.addWidget(map_button)

        capacity = QWidget()
        meters = QHBoxLayout(capacity)
        meters.setContentsMargins(0, 0, 0, 0)
        meters.setSpacing(16)

        def add_meter(name):
            label = QLabel()
            label.setObjectName("muted")
            bar = QProgressBar()
            bar.setObjectName("deviceCapacity")
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            bar.setAccessibleName(name)
            column = QVBoxLayout()
            column.setSpacing(3)
            column.addWidget(label)
            column.addWidget(bar)
            meters.addLayout(column, 1)
            return label, bar

        slots_label, slots_bar = add_meter("Occupied sample slots")
        memory_label, memory_bar = add_meter("Used sample memory")
        capacity.hide()
        section.insertWidget(1, capacity, 1)
        layout.addLayout(section)

        slot_map = self.device_slot_map = QWidget()
        slot_map.setObjectName("deviceSlotMap")
        slot_map.hide()
        grid = QGridLayout(slot_map)
        grid.setContentsMargins(1, 1, 1, 1)
        grid.setSpacing(2)
        map_cells = []
        columns = 25
        for number in range(200):
            cell = DeviceDropButton(f"{number:03d}", slot=number)
            cell.sample_dropped.connect(lambda sample_id, n=number: send_to_slot(n, sample_id))
            cell.setObjectName("deviceSlotCell")
            cell.setCheckable(True)
            cell.setFocusPolicy(Qt.NoFocus)
            cell.setFixedHeight(16)
            cell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid.addWidget(cell, number // columns, number % columns)
            map_cells.append(cell)
        layout.addWidget(slot_map)
        map_transition = QVariantAnimation(slot_map)
        map_transition.setDuration(150)
        map_transition.setEasingCurve(QEasingCurve.OutCubic)
        map_transition.valueChanged.connect(slot_map.setMaximumHeight)

        def finish_map_transition():
            slot_map.setVisible(map_button.isChecked())
            slot_map.setMaximumHeight(16777215)

        def toggle_map(expanded):
            height = slot_map.height() if not slot_map.isHidden() else 0
            map_transition.stop()
            if not self.device_page.isVisible():
                finish_map_transition()
                return
            slot_map.setMaximumHeight(height)
            slot_map.show()
            map_transition.setStartValue(height)
            map_transition.setEndValue(grid.sizeHint().height() if expanded else 0)
            map_transition.start()

        map_transition.finished.connect(finish_map_transition)
        map_button.toggled.connect(toggle_map)

        def show_drop_map():
            if self.device_slots_current:
                map_button.setChecked(True)
                self.switch_page(1)

        self.device_button.sample_entered.connect(show_drop_map)

        def send_to_slot(number, sample_id):
            if self.device_slots_current:
                self.slots_page.send_dropped_sample(number, sample_id)

        filters = QHBoxLayout()
        search = QLineEdit()
        translated(search, "Search by slot number or sample name", "setPlaceholderText")
        search.setClearButtonEnabled(True)
        filters.addWidget(search, 1)
        status = QComboBox()
        captions = ("All slots", "Occupied", "Free")
        status.addItems(captions)
        translated(status, captions, "items")
        filters.addWidget(status)
        layout.addLayout(filters)

        view = QStackedWidget()
        placeholder = QFrame()
        placeholder.setObjectName("panel")
        placeholder_layout = QVBoxLayout(placeholder)
        empty = self.device_empty_label = translated(QLabel(), "Connect Volca by USB to read its sample slots.")
        empty.setObjectName("muted")
        empty.setAlignment(Qt.AlignCenter)
        empty.setWordWrap(True)
        placeholder_layout.addWidget(empty)
        view.addWidget(placeholder)

        table = self.device_table = QTableWidget(0, 3)
        table.setObjectName("deviceSampleTable")
        translated(table, ("Slot", "Sample", "Duration"), "setHorizontalHeaderLabels")
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setShowGrid(False)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(28 if self.compact else 30)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.setColumnWidth(0, 86)
        table.setColumnWidth(2, 110)
        table.overlay_scroll = OverlayScrollBar(table)
        view.addWidget(table)
        layout.addWidget(view, 1)
        usage_label = QLabel()
        usage_label.setObjectName("muted")
        usage_label.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
        usage_label.setWordWrap(True)
        usage_label.hide()
        layout.addWidget(usage_label)

        footer = QHBoxLayout()
        notice = self.device_operation_status = QLabel()
        notice.setObjectName("muted")
        notice.setWordWrap(True)
        footer.addWidget(last_read)
        footer.addWidget(notice, 1)
        self.device_preview_player = AudioPlayer()
        self.device_preview_timer = QTimer(self)
        self.device_preview_timer.setInterval(100)
        self.device_preview_timer.timeout.connect(self._check_device_preview)
        self.device_preview_buttons = {}
        self.device_playing_slot = None
        add = translated(QPushButton(), "Add to Library")
        sample_actions = translated(QPushButton(), "Sample actions")
        sample_actions.setProperty("menuButton", True)
        sample_menu = QMenu(sample_actions)
        rename = translated_action(sample_menu, "Rename on Volca…")
        save = sample_menu.addAction(tr("Save selected WAV…"))
        sample_menu.addSeparator()
        delete = translated_action(sample_menu, "Delete from Volca…")
        sample_actions.setMenu(sample_menu)
        self.device_slot_actions = (add, sample_actions, backup_slots)
        for button in (add, sample_actions):
            size = button.sizePolicy()
            size.setRetainSizeWhenHidden(True)
            button.setSizePolicy(size)
            button.hide()
            footer.addWidget(button)
        self._set_device_slots_current(False)
        layout.addLayout(footer)

        def selected_item():
            row = table.currentRow()
            if row >= 0 and table.selectionModel().isRowSelected(row):
                return table.item(row, 0)
            return None

        def refresh_pattern_usage():
            item = selected_item()
            selection = item.data(Qt.UserRole) if item is not None else None
            usage_label.setVisible(bool(selection and selection[1] and self.device_slots_current))
            if selection is None or not selection[1]:
                return
            usage = self._device_pattern_usage
            if usage is None:
                usage_label.setText(f'<a href="read">{tr("Check which patterns use this slot")}</a>')
                return
            numbers = usage.get(selection[0], [])
            if numbers:
                links = ", ".join(f'<a href="{number}">{number:02d}</a>' for number in numbers)
                label = tr("Used in patterns: {patterns}").format(patterns=links)
            else:
                label = tr("No stored patterns use this slot")
            usage_label.setText(f'{label} · <a href="read">{tr("Refresh")}</a>')

        def read_pattern_usage():
            try:
                items = self._run_device_operation(tr("Reading patterns from Volca…"), self._read_live_patterns)
            except Exception:
                self._set_device_pattern_usage(None)
                raise
            self._set_device_pattern_usage(items)

        def follow_pattern_link(link):
            if link != "read":
                self._open_device_pattern(int(link))
                return
            try:
                read_pattern_usage()
            except Exception as exc:
                QMessageBox.warning(self, tr("Could not read patterns"), str(exc))

        self._refresh_device_pattern_usage = refresh_pattern_usage
        usage_label.linkActivated.connect(follow_pattern_link)

        def update_selection():
            refresh_pattern_usage()
            self._stop_device_preview()
            item = selected_item()
            number = item.data(Qt.UserRole)[0] if item is not None else None
            for cell_number, cell in enumerate(map_cells):
                cell.setChecked(number == cell_number)
            if item is None:
                add.hide()
                sample_actions.hide()
                volca_panel.set_selection()
                return
            number, occupied = item.data(Qt.UserRole)
            add.setVisible(occupied)
            sample_actions.setVisible(occupied)
            volca_panel.set_selection(number, table.item(item.row(), 1).text())

        def update_table():
            item = selected_item()
            selected = item.data(Qt.UserRole)[0] if item is not None else None
            query = search.text().strip().casefold()
            mode = status.currentIndex()
            with QSignalBlocker(table):
                populate_table(selected, query, mode)
            update_selection()
            if not table.rowCount():
                translated(empty, "No slots match your search or filter.")
            view.setCurrentWidget(table if table.rowCount() else placeholder)

        def populate_table(selected, query, mode):
            self._stop_device_preview()
            self.device_preview_buttons.clear()
            table.setRowCount(0)
            for number, name, duration in slots:
                occupied = duration is not None
                if (mode == 1 and not occupied) or (mode == 2 and occupied):
                    continue
                matches_number = query.isdecimal() and query.lstrip("0") == str(number).lstrip("0")
                if query and not matches_number and query not in name.casefold():
                    continue
                row = table.rowCount()
                table.insertRow(row)
                duration_text = "—"
                if occupied:
                    duration_text = tr("< 0.01 s") if duration < 0.01 else f"{duration:.2f} s"
                cells = (f"{number:03d}", (name or tr("Unnamed sample")) if occupied else tr("Free"),
                         duration_text)
                for column, value in enumerate(cells):
                    item = QTableWidgetItem(value)
                    if column == 0:
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    elif column == 2:
                        item.setTextAlignment(Qt.AlignCenter)
                    if column == 1 and occupied:
                        item.setData(Qt.UserRole, name)
                        item.setToolTip(value)
                    table.setItem(row, column, item)
                table.item(row, 0).setData(Qt.UserRole, (number, occupied))
                if occupied:
                    button = QPushButton("▶")
                    button.setObjectName("samplePlay")
                    button.setFixedSize(32, 24)
                    button.setAccessibleName(f"Play slot {number:03d}")
                    button.setToolTip(f"Play slot {number:03d} on this Mac")
                    button.setEnabled(self.device_slots_current)
                    button.clicked.connect(
                        lambda checked=False, r=row, n=number: preview_slot(r, n)
                    )
                    self.device_preview_buttons[number] = button
                    controls = QWidget()
                    controls.setObjectName("deviceSlotControls")
                    controls.setStyleSheet("QWidget#deviceSlotControls { background: transparent; }")
                    controls_layout = QHBoxLayout(controls)
                    controls_layout.setContentsMargins(6, 0, 0, 0)
                    controls_layout.addWidget(button)
                    controls_layout.addStretch()
                    table.setCellWidget(row, 0, controls)
                if number == selected:
                    table.selectRow(row)

        def select_map_slot(number):
            def select_visible():
                for row in range(table.rowCount()):
                    item = table.item(row, 0)
                    if item.data(Qt.UserRole)[0] == number:
                        table.selectRow(row)
                        table.scrollToItem(item)
                        map_cells[number].setChecked(True)
                        return True
                return False

            if not select_visible():
                with QSignalBlocker(search), QSignalBlocker(status):
                    search.clear()
                    status.setCurrentIndex(0)
                update_table()
                select_visible()

        for number, cell in enumerate(map_cells):
            cell.clicked.connect(lambda checked=False, slot=number: select_map_slot(slot))

        def refresh_slots():
            try:
                rows, memory = self._read_device_slots(with_memory=True)
            except Exception as exc:
                self._set_device_slots_current(False)
                if slots:
                    last_read.setText("Last read · Refresh failed")
                if is_identify_timeout(exc):
                    connection_notice.show()
                    translated(refresh, "Retry connection")
                else:
                    QMessageBox.warning(self, tr("Could not read Volca slots"), str(exc))
                return
            apply_slot_overview(rows, memory)

        def apply_slot_overview(rows, memory):
            nonlocal slots
            slots = rows
            occupied = sum(duration is not None for _, _, duration in slots)
            slots_label.setText(tr("{used} used · {free} free").format(used=occupied, free=len(slots) - occupied))
            slots_bar.setRange(0, max(1, len(slots)))
            slots_bar.setValue(occupied)
            if memory is None:
                memory_label.setText(tr("Memory · unavailable"))
                memory_bar.setRange(0, 1)
                memory_bar.setValue(0)
            else:
                used, total = memory
                memory_label.setText(tr("Memory · {free} KiB free").format(free=(total - used) // 1024))
                memory_bar.setRange(0, total)
                memory_bar.setValue(used)
            memory_bar.setToolTip(memory_label.text() if memory is None else
                                  f"{used // 1024} of {total // 1024} KiB used")
            capacity.show()
            last_read.setText(tr("Read at {time}").format(time=datetime.now().strftime("%H:%M")))
            notice.clear()
            connection_notice.hide()
            for number, name, duration in slots:
                cell = map_cells[number]
                occupied_slot = duration is not None
                cell.setProperty("occupied", occupied_slot)
                description = name if occupied_slot and name else tr("Unnamed sample" if occupied_slot else "Free")
                cell.setToolTip(f"{number:03d} · {description}")
                cell.setAccessibleName(cell.toolTip())
                cell.style().unpolish(cell)
                cell.style().polish(cell)
            map_button.show()
            translated(refresh, "Refresh slots")
            self._set_device_slots_current(True)
            backup_slots.setEnabled(occupied > 0)
            search.setEnabled(True)
            status.setEnabled(True)
            update_table()

        self._apply_device_slot_overview = apply_slot_overview

        def save_selected():
            item = selected_item()
            if item is not None:
                number, occupied = item.data(Qt.UserRole)
                if occupied:
                    self.save_device_sample(self, number)

        def rename_selected():
            item = selected_item()
            if item is None or not self.device_slots_current:
                return
            number, occupied = item.data(Qt.UserRole)
            if not occupied:
                return
            index = next(i for i, (slot, _, _) in enumerate(slots) if slot == number)
            _, old_name, duration = slots[index]
            name = self._ask_device_name(f"Rename sample {number:03d} on Volca", old_name, 24)
            if name is None or name == old_name:
                return
            self._stop_device_preview()
            if self._edit_device_sample(number, old_name, duration, name):
                slots[index] = (number, name, duration)
                update_table()

        def delete_selected():
            item = selected_item()
            if item is None or not self.device_slots_current:
                return
            number, occupied = item.data(Qt.UserRole)
            if not occupied:
                return
            _, name, duration = next(row for row in slots if row[0] == number)
            try:
                read_pattern_usage()
            except Exception:
                pass
            usage = self._device_pattern_usage
            message = tr("Clear slot {slot} on Volca? A recovery copy will be saved first.").format(slot=f"{number:03d}")
            if usage is None:
                message += "\n\n" + tr("Could not check which patterns use this slot.")
            elif number in usage:
                used_by = ", ".join(f"{pattern:02d}" for pattern in usage[number])
                message += "\n\n" + tr("Used in patterns: {patterns}").format(patterns=used_by)
            if QMessageBox.question(
                self, tr("Delete sample from Volca"),
                message,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                return
            self._stop_device_preview()
            recovery = self._edit_device_sample(number, name, duration, None)
            if recovery is not None:
                self._mark_device_slots_changed()
                self._show_recovery_result(
                    "Sample deleted", f"Slot {number:03d} is empty.",
                    QMessageBox.Information, recovery, self.preview_sample_restore,
                )

        def add_selected():
            item = selected_item()
            if item is None:
                return
            number, occupied = item.data(Qt.UserRole)
            if not occupied:
                return
            name = table.item(item.row(), 1).data(Qt.UserRole) or f"Volca {number:03d}"
            self._stop_device_preview()
            try:
                with tempfile.TemporaryDirectory(prefix="VolcaStudio-import-") as folder:
                    path = Path(folder) / "sample.wav"
                    source = self._run_device_operation(
                        f"Reading sample {number:03d} from Volca…", self._prepare_device_audio, number, path,
                    )
                    sample = self.library.find_identical_wav(path, name)
                    already_added = sample is not None
                    if not already_added:
                        sample = self.library.add(source, path, name=name)
            except Exception as exc:
                QMessageBox.warning(self, tr("Could not add sample to library"), str(exc))
                return
            if not already_added:
                self.slots_page.refresh()
            result = QMessageBox(self)
            result.setWindowTitle(tr("Already in Library") if already_added else tr("Added to Library"))
            result.setTextFormat(Qt.PlainText)
            result.setIcon(QMessageBox.Information)
            result.setText(f"“{name}” is already in Samples." if already_added
                           else f"“{name}” is now in Samples.")
            done = result.addButton(QMessageBox.Ok)
            show_sample = result.addButton("Show in Samples", QMessageBox.ActionRole)
            result.setDefaultButton(done)
            result.exec()
            if result.clickedButton() == show_sample:
                self.samples_button.click()
                self.slots_page.focus_library_sample(sample["id"])

        def preview_slot(row, number):
            if self.device_playing_slot == number and self.device_preview_player.is_playing():
                self._stop_device_preview()
                return
            table.selectRow(row)
            self._stop_device_preview()
            try:
                with tempfile.TemporaryDirectory(prefix="VolcaStudio-preview-") as folder:
                    path = Path(folder) / "preview.wav"
                    self._run_device_operation(
                        f"Reading sample {number:03d} from Volca…", self._prepare_device_audio, number, path,
                    )
                    self.device_preview_player.play(str(path))
            except Exception as exc:
                QMessageBox.warning(self, tr("Could not play sample"), str(exc))
                return
            self.device_playing_slot = number
            self._update_device_preview_button(number, playing=True)
            self.device_preview_timer.start()

        search.setEnabled(False)
        status.setEnabled(False)
        search.textChanged.connect(update_table)
        status.currentIndexChanged.connect(update_table)
        refresh.clicked.connect(refresh_slots)
        table.itemSelectionChanged.connect(update_selection)
        add.clicked.connect(add_selected)
        save.triggered.connect(save_selected)
        rename.triggered.connect(rename_selected)
        delete.triggered.connect(delete_selected)

    def _mark_device_slots_changed(self):
        self._invalidate_pattern_samples()
        if self.device_last_read.text():
            self.device_last_read.setText("Last read · Refresh after changes")
            self._set_device_slots_current(False)

    def _set_device_slots_current(self, current):
        self.device_slots_current = current
        if hasattr(self, "_refresh_device_pattern_usage"):
            self._refresh_device_pattern_usage()
        self.device_slot_map.setEnabled(current)
        if not current:
            self._stop_device_preview()
        for action in self.device_slot_actions:
            action.setEnabled(current)
        for button in self.device_preview_buttons.values():
            button.setEnabled(current)

    def _check_device_preview(self):
        if not self.device_preview_player.is_playing():
            self._stop_device_preview()

    def _stop_device_preview(self):
        self.device_preview_timer.stop()
        self.device_preview_player.stop()
        if self.device_playing_slot is not None:
            self._update_device_preview_button(self.device_playing_slot, playing=False)
            self.device_playing_slot = None

    def _update_device_preview_button(self, slot, playing):
        button = self.device_preview_buttons[slot]
        action = tr("Stop") if playing else tr("Play")
        button.setText("■" if playing else "▶")
        button.setAccessibleName(f"{action} slot {slot:03d}")
        button.setToolTip(f"{action} slot {slot:03d} on this Mac")

    def _build_connection_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Connection & diagnostics"))
        dialog.resize(620, 440)
        self.connection_dialog = dialog
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        section = QLabel("MIDI ports")
        section.setObjectName("section")
        layout.addWidget(section)

        layout.addWidget(QLabel("MIDI INPUT"))
        self.input_combo = QComboBox()
        layout.addWidget(self.input_combo)

        layout.addWidget(QLabel("MIDI OUTPUT"))
        self.output_combo = QComboBox()
        layout.addWidget(self.output_combo)

        buttons = QHBoxLayout()

        self.refresh_button = QPushButton(tr("Refresh ports"))
        self.refresh_button.clicked.connect(self.refresh_ports)
        buttons.addWidget(self.refresh_button)

        self.connect_button = QPushButton(tr("Open selected ports"))
        self.connect_button.clicked.connect(self.open_selected_ports)
        buttons.addWidget(self.connect_button)

        self.disconnect_button = QPushButton(tr("Close ports"))
        self.disconnect_button.clicked.connect(self.close_ports)
        self.disconnect_button.setEnabled(False)
        buttons.addWidget(self.disconnect_button)

        layout.addLayout(buttons)

        diagnostics = QLabel(tr("Diagnostics"))
        diagnostics.setObjectName("section")
        layout.addWidget(diagnostics)

        status_row = QHBoxLayout()

        self.input_status = QLabel("Input: waiting")
        self.input_status.setObjectName("muted")

        self.output_status = QLabel("Output: waiting")
        self.output_status.setObjectName("muted")

        status_row.addWidget(self.input_status)
        status_row.addStretch()
        status_row.addWidget(self.output_status)

        layout.addLayout(status_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setPlaceholderText(
            "MIDI diagnostic events will appear here."
        )
        layout.addWidget(self.log_view, 1)

        close = translated(QPushButton(), "Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, alignment=Qt.AlignRight)

    def _log(self, message: str):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {message}")
        logger.info(message)

    def refresh_ports(self):
        current_in = self.input_combo.currentText()
        current_out = self.output_combo.currentText()

        try:
            inputs = self.midi.input_names()
            outputs = self.midi.output_names()
        except Exception as exc:
            self._log(f"Could not enumerate MIDI ports: {exc}")
            QMessageBox.warning(self, tr("MIDI error"), str(exc))
            return

        self.input_combo.clear()
        self.output_combo.clear()

        self.input_combo.addItems(inputs)
        self.output_combo.addItems(outputs)

        if current_in in inputs:
            self.input_combo.setCurrentText(current_in)

        if current_out in outputs:
            self.output_combo.setCurrentText(current_out)

        self._log(
            f"Port scan: {len(inputs)} input(s), "
            f"{len(outputs)} output(s)"
        )
        self._update_status()

    def _check_ports(self):
        try:
            inputs = self.midi.input_names()
            outputs = self.midi.output_names()

            selected_in = self.input_combo.currentText()
            selected_out = self.output_combo.currentText()

            if self.midi.input_open and selected_in not in inputs:
                self.midi.close_input()
                self._log("Selected MIDI input disappeared; closed it.")

            if self.midi.output_open and selected_out not in outputs:
                self.midi.close_output()
                self._log("Selected MIDI output disappeared; closed it.")

            known_in = {
                self.input_combo.itemText(i)
                for i in range(self.input_combo.count())
            }
            known_out = {
                self.output_combo.itemText(i)
                for i in range(self.output_combo.count())
            }

            if set(inputs) != known_in or set(outputs) != known_out:
                self.refresh_ports()
            else:
                self._update_status()

        except Exception as exc:
            self._log(f"Port monitoring error: {exc}")

    def open_selected_ports(self):
        try:
            input_name = self.input_combo.currentText()
            output_name = self.output_combo.currentText()

            if not input_name and not output_name:
                QMessageBox.information(
                    self,
                    "Select a port",
                    "No MIDI ports are available. "
                    "Connect the Volca by USB and refresh."
                )
                return

            if input_name:
                self.midi.open_input(input_name)
                self._log(f"Opened MIDI input: {input_name}")

            if output_name:
                self.midi.open_output(output_name)
                self._log(
                    f"Opened MIDI output: {output_name} (no data sent)"
                )

            self._update_status()

        except Exception as exc:
            self._log(f"Could not open MIDI port: {exc}")
            QMessageBox.warning(
                self, "MIDI connection error", str(exc)
            )
            self._update_status()

    def close_ports(self):
        self.midi.close()
        self._log("MIDI ports closed.")
        self._update_status()

    def _incoming_message(self, message: str):
        self.midi_message_received.emit(message)

    def _append_incoming(self, message: str):
        self._log(f"IN  {message}")

    def _update_status(self):
        in_open = self.midi.input_open
        out_open = self.midi.output_open

        self.input_status.setText(
            "Input: open" if in_open else "Input: waiting"
        )
        self.output_status.setText(
            "Output: open" if out_open else "Output: waiting"
        )

        def volca_count(combo):
            return sum("volca sample" in combo.itemText(index).casefold()
                       for index in range(combo.count()))

        detected = volca_count(self.input_combo) == volca_count(self.output_combo) == 1
        translated(self.connection_badge, "● Volca detected" if detected else "● Volca not detected")
        self._style_connection_badge(detected)
        self.volca_panel.set_connected(detected)
        if detected != self.device_read_button.isEnabled():
            translated(self.device_empty_label, "Click Read slots to see the samples on Volca." if detected
                       else "Connect Volca by USB to read its sample slots.")
        if not detected and self.device_read_button.isEnabled() and self.device_last_read.text():
            self.device_last_read.setText("Last read · Volca disconnected")
            self._set_device_slots_current(False)
        if not detected and self.read_live_patterns_button.isEnabled():
            self._invalidate_pattern_comparison()
        self.device_read_button.setEnabled(detected)
        self._update_pattern_connection(detected)
        self.device_clear_action.setEnabled(detected)

        self.disconnect_button.setEnabled(in_open or out_open)

    def closeEvent(self, event):
        self._stop_pattern_preview()
        self.slots_page.stop_playback()
        self._stop_device_preview()
        self.timer.stop()
        self.midi.close()
        event.accept()
