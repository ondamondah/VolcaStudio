from app.ui.translations import tr
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from time import monotonic
from PySide6.QtCore import QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QMenu,
    QMessageBox, QPushButton, QScrollBar, QVBoxLayout, QWidget,
)
from app.ui.components.fade_waveform import FadeWaveform
from app.audio.fades import apply_fades
from app.audio.editor import copy_region_frames, cut_region
from app.audio.player import AudioPlayer
from app.audio.slicing import beat_markers, estimate_beat_grid, transient_markers


class SampleEditor(QDialog):
    def __init__(self, sample, library, parent=None, *, send_fragments=None):
        super().__init__(parent)
        self.sample, self.library = sample, library
        self.send_fragments = send_fragments
        self.setWindowTitle(tr('Sample editor — ') + sample['name'])
        self.resize(850, 420)
        self.player = AudioPlayer()
        self.temp = TemporaryDirectory(prefix='volcastudio_edit_')
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self.tick)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        self.waveform = FadeWaveform()
        self.previous_slices = None
        self.pad_page = 0
        self.waveform.fade_finished.connect(self.save_fades)
        self.waveform.fade_started.connect(self.stop)
        self.waveform.selection_changed.connect(self.selection_changed)
        self.waveform.slices_changed.connect(self.follow_slice)
        self.waveform.slice_selected.connect(self.follow_slice)
        self.slice_preview_timer = QTimer(self)
        self.slice_preview_timer.setSingleShot(True)
        self.slice_preview_timer.timeout.connect(self.play_slice)
        self.waveform.slice_play_requested.connect(
            lambda: self.slice_preview_timer.start(QApplication.doubleClickInterval()),
        )
        self.waveform.slices_changed.connect(self.slice_preview_timer.stop)

        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(5)
        self.play_button = QPushButton(tr('▶ Play'))
        self.play_button.setAutoDefault(False)
        self.play_button.clicked.connect(self.toggle_playback)
        zoom_row.addWidget(self.play_button)
        zoom_row.addSpacing(8)
        for text, hint, action in [
            ('−', 'Zoom out', lambda: self.waveform.zoom(0.5)),
            ('+', 'Zoom in', lambda: self.waveform.zoom(2)),
            (tr('Fit'), 'Show the whole sample', lambda: self.waveform.set_view(0, 1)),
        ]:
            button = QPushButton(tr(text))
            button.setAutoDefault(False)
            button.setToolTip(hint)
            button.clicked.connect(action)
            zoom_row.addWidget(button)
        self.zoom_label = QLabel()
        self.zoom_label.setObjectName("muted")
        zoom_row.addWidget(self.zoom_label)
        zoom_row.addStretch()
        mode_group = QButtonGroup(self)
        mode_group.setExclusive(True)
        modes = QHBoxLayout()
        modes.setSpacing(1)
        for mode, caption in (('trim', 'Trim'), ('fade', 'Fades'), ('slice', 'Slice')):
            button = QPushButton(tr(caption))
            button.setObjectName('editorMode')
            button.setAutoDefault(False)
            button.setCheckable(True)
            mode_group.addButton(button)
            modes.addWidget(button)
            if mode == 'trim':
                button.setChecked(True)
            button.toggled.connect(lambda checked, value=mode: checked and self.set_mode(value))
        zoom_row.addLayout(modes)
        layout.addLayout(zoom_row)
        layout.addWidget(self.waveform, 1)

        self.scroll = QScrollBar(Qt.Horizontal)
        self.scroll.setRange(0, 100000)
        self.scroll.valueChanged.connect(self.scroll_wave)
        layout.addWidget(self.scroll)
        self.waveform.view_changed.connect(self.update_zoom)
        self.selection_label = QLabel()
        self.selection_label.setObjectName("muted")


        self.pad_panel = QWidget()
        pad_layout = QVBoxLayout(self.pad_panel)
        pad_layout.setContentsMargins(0, 0, 0, 0)
        tools = QHBoxLayout()
        automatic = QPushButton(tr('Auto-slice'))
        automatic.setAutoDefault(False)
        automatic.setProperty("menuButton", True)
        modes = QMenu(automatic)
        modes.addAction(tr('Slice by beat…'), self.show_beat_controls)
        modes.addAction(tr('Find attacks'), lambda: self.find_attacks())
        sensitivity = modes.addMenu(tr('Attack sensitivity'))
        sensitivity.addAction(tr('Strong hits'), lambda: self.find_attacks('strong'))
        sensitivity.addAction(tr('Detailed'), lambda: self.find_attacks('detailed'))
        modes.addSeparator()
        for count in (4, 8, 16, 32):
            modes.addAction(tr('{count} equal slices').format(count=count),
                            lambda checked=False, n=count: self.slice_evenly(n))
        automatic.setMenu(modes)
        tools.addWidget(automatic)
        self.undo_auto = QPushButton('↶')
        self.undo_auto.setToolTip(tr('Undo auto-slice'))
        self.undo_auto.setAccessibleName(tr('Undo auto-slice'))
        self.undo_auto.setAutoDefault(False)
        self.undo_auto.clicked.connect(self.undo_auto_slice)
        tools.addWidget(self.undo_auto)
        self.find_tempo_button = QPushButton(tr('Find tempo'))
        self.find_tempo_button.setAutoDefault(False)
        self.find_tempo_button.clicked.connect(self.find_tempo)
        tools.addWidget(self.find_tempo_button)
        self.find_tempo_button.hide()
        tools.addSpacing(8)
        tools.addWidget(self.selection_label, 1)
        self.pad_back = QPushButton('‹')
        self.pad_next = QPushButton('›')
        self.pad_range = QLabel()
        self.pad_range.setObjectName("muted")
        self.pad_range.setAlignment(Qt.AlignCenter)
        self.pad_range.setMinimumWidth(48)
        self.pad_back.setAccessibleName(tr('Previous pads'))
        self.pad_next.setAccessibleName(tr('Next pads'))
        for button, step in ((self.pad_back, -1), (self.pad_next, 1)):
            button.setAutoDefault(False)
            button.clicked.connect(lambda checked=False, delta=step: self.change_pad_page(delta))
        tools.addWidget(self.pad_back)
        tools.addWidget(self.pad_range)
        tools.addWidget(self.pad_next)
        pad_layout.addLayout(tools)
        self.beat_controls = QWidget()
        beat_row = QHBoxLayout(self.beat_controls)
        beat_row.setContentsMargins(0, 0, 0, 0)
        self.tempo = QDoubleSpinBox()
        self.tempo.setRange(20, 400)
        self.tempo.setDecimals(2)
        self.tempo.setSuffix(' BPM')
        self.tempo.setAccessibleName('BPM')
        tempo_match = re.search(r'(?<![\d.])(\d+(?:[.,]\d+)?)\s*bpm\b', sample['name'], re.IGNORECASE)
        suggested = float(tempo_match[1].replace(',', '.')) if tempo_match else 120
        self.tempo_hint = suggested if tempo_match and 55 <= suggested <= 200 else None
        self.tempo.setValue(suggested if 20 <= suggested <= 400 else 120)
        self.tempo.valueChanged.connect(self.update_beat_grid)
        beat_row.addWidget(self.tempo)
        for text, factor, description in (
            ('÷2', 0.5, 'Halve BPM'),
            ('×2', 2, 'Double BPM'),
        ):
            button = QPushButton(text)
            button.setAutoDefault(False)
            button.setToolTip(tr(description))
            button.setAccessibleName(tr(description))
            button.clicked.connect(
                lambda checked=False, scale=factor: self.tempo.setValue(self.tempo.value() * scale),
            )
            self.tempo.valueChanged.connect(
                lambda bpm, scale=factor, control=button: control.setEnabled(
                    self.tempo.minimum() <= bpm * scale <= self.tempo.maximum(),
                ),
            )
            button.setEnabled(self.tempo.minimum() <= self.tempo.value() * factor <= self.tempo.maximum())
            beat_row.addWidget(button)
        self.beat_division = QComboBox()
        self.beat_division.setAccessibleName(tr('Slice length'))
        for label, beats in (('Quarter notes', 1), ('Eighth notes', 0.5), ('One bar (4/4)', 4)):
            self.beat_division.addItem(tr(label), beats)
        self.beat_division.currentIndexChanged.connect(self.update_beat_grid)
        beat_row.addWidget(self.beat_division)
        self.beat_anchor = QPushButton()
        self.beat_anchor.setAutoDefault(False)
        self.beat_anchor.setCheckable(True)
        self.beat_anchor.setToolTip(tr('Click the waveform to set the first beat'))
        self.beat_anchor.toggled.connect(self.place_first_beat)
        self.waveform.first_beat_set.connect(self.update_first_beat)
        self.waveform.beat_drag_started.connect(self.stop)
        self.waveform.beat_preview_requested.connect(self.play_beat_interval)
        beat_row.addWidget(self.beat_anchor)
        beat_row.addStretch()
        preview_beat = QPushButton('▶')
        preview_beat.setAutoDefault(False)
        preview_beat.setAccessibleName(tr('Play grid interval'))
        preview_beat.setToolTip(tr('Play grid interval'))
        preview_beat.clicked.connect(self.play_beat_interval)
        beat_row.addWidget(preview_beat)
        apply_beats = QPushButton(tr('Slice by beat'))
        apply_beats.setAutoDefault(False)
        apply_beats.clicked.connect(self.slice_by_beat)
        beat_row.addWidget(apply_beats)
        close_beats = QPushButton('×')
        close_beats.setAutoDefault(False)
        close_beats.setAccessibleName(tr('Close beat controls'))
        close_beats.clicked.connect(self.hide_beat_controls)
        beat_row.addWidget(close_beats)
        pad_layout.addWidget(self.beat_controls)
        self.beat_controls.hide()
        self.update_first_beat(0)

        grid = QGridLayout()
        grid.setSpacing(4)
        self.pads = []
        self.pad_keys = []
        self.pad_key_labels = []
        for index, (key, alias) in enumerate(zip('12345678QWERTYUI', '12345678ЙЦУКЕНГШ')):
            pad = QPushButton()
            pad.setObjectName('slicePad')
            pad.setAutoDefault(False)
            pad.setCheckable(True)
            pad.setMinimumHeight(38)
            pad.clicked.connect(lambda checked=False, i=index: self.trigger_pad(i))
            key_label = QLabel(key, pad)
            key_label.setObjectName('slicePadKey')
            key_label.setAttribute(Qt.WA_TransparentForMouseEvents)
            key_layout = QHBoxLayout(pad)
            key_layout.setContentsMargins(3, 1, 5, 2)
            key_layout.addWidget(key_label, 0, Qt.AlignRight | Qt.AlignBottom)
            self.pad_key_labels.append(key_label)
            shortcut = QShortcut(QKeySequence(key), self)
            if alias != key:
                shortcut.setKeys([QKeySequence(key), QKeySequence(alias)])
            shortcut.setAutoRepeat(False)
            shortcut.setEnabled(False)
            shortcut.activated.connect(lambda i=index: self.trigger_pad(i))
            grid.addWidget(pad, index // 8, index % 8)
            self.pads.append(pad)
            self.pad_keys.append(shortcut)
        pad_layout.addLayout(grid)
        layout.addWidget(self.pad_panel)
        self.pad_panel.hide()

        row = QHBoxLayout()
        self.trim_label = QLabel()
        self.trim_label.setObjectName("muted")
        layout.addWidget(self.trim_label)
        self.buttons = {}
        for text, action in [('▶ Selection', self.play_selection),
                             ('Keep selection', lambda: self.edit(True)),
                             ('Delete selection', lambda: self.edit(False)), ('Undo', self.undo)]:
            button = QPushButton(tr(text))
            button.setAutoDefault(False)
            button.clicked.connect(action)
            row.addWidget(button)
            self.buttons[text] = button
            if text == '▶ Selection':
                row.addStretch()
        self.trim_actions = QWidget()
        row.setContentsMargins(0, 0, 0, 0)
        self.trim_actions.setLayout(row)
        layout.addWidget(self.trim_actions)

        footer = QHBoxLayout()
        self.slice_included = QCheckBox(tr('Include fragment'))
        self.slice_included.toggled.connect(self.waveform.set_slice_included)

        self.add_slices = QPushButton()
        self.add_slices.setAutoDefault(False)
        self.add_slices.clicked.connect(self.save_slices)
        self.add_slices.setObjectName("primaryAction")
        self.slice_included.hide()
        self.add_slices.hide()

        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.status.setToolTip("Edits update every slot using this sample. Undo restores the previous version.")
        footer.addWidget(self.status, 1)
        footer.addWidget(self.slice_included)
        footer.addWidget(self.add_slices)
        self.send_slices_button = QPushButton(tr('Send to Volca…'))
        self.send_slices_button.setAutoDefault(False)
        self.send_slices_button.clicked.connect(self.send_slices)
        self.send_slices_button.hide()
        footer.addWidget(self.send_slices_button)
        close = QPushButton(tr('Close'))
        close.setAutoDefault(False)
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)
        self.reload()
        QApplication.instance().focusChanged.connect(self.update_pad_shortcuts)

    def update_zoom(self, start, end):
        span = end - start
        self.zoom_label.setText(f"{1 / span:.0f}× · {start * self.sample['duration']:.3f}–{end * self.sample['duration']:.3f} s")
        self.scroll.blockSignals(True)
        self.scroll.setRange(0, round((1 - span) * 100000))
        self.scroll.setPageStep(round(span * 100000))
        self.scroll.setValue(round(start * 100000))
        self.scroll.setVisible(span < 1 - 1e-6)
        self.scroll.blockSignals(False)

    def scroll_wave(self, value):
        span = self.waveform.view_end - self.waveform.view_start
        self.waveform.set_view(value / 100000, value / 100000 + span)

    def set_mode(self, mode):
        self.stop()
        slicing = mode == 'slice'
        wave = self.waveform
        wave.fade_mode = mode == 'fade'
        wave.slice_mode = slicing
        wave.slice_drag = None
        if not slicing:
            self.beat_anchor.setChecked(False)
            wave.beat_drag_offset = None
        wave.selection_start = wave.selection_end = 0
        wave.update()
        self.trim_actions.setVisible(not slicing)
        for name, button in self.buttons.items():
            button.setVisible(mode == 'trim' or name == 'Undo')
        self.trim_label.setVisible(mode != 'slice')
        self.pad_panel.setVisible(slicing)
        self.slice_included.setVisible(slicing)
        self.add_slices.setVisible(slicing)
        self.send_slices_button.setVisible(slicing and self.send_fragments is not None)
        self.status.clear()
        if wave.fade_mode:
            self.status.setText(tr('Drag the handles to adjust fades'))
        if slicing:
            self.follow_slice()
            wave.setToolTip(tr('Double-click to split · Drag markers to adjust · Click a pad to listen'))
        else:
            for shortcut in self.pad_keys:
                shortcut.setEnabled(False)
            self.selection_changed(0, 0)
            wave.setToolTip(tr('Drag the handles to adjust fades') if wave.fade_mode else
                            tr('Drag on the waveform to select a region'))

    def follow_slice(self):
        self.pad_page = self.waveform.slice_index // len(self.pads)
        self.update_slices()

    def change_pad_page(self, delta):
        self.pad_page += delta
        self.update_slices()

    def update_pad_shortcuts(self, *_):
        offset = self.pad_page * len(self.pads)
        count = len(self.waveform.slice_regions())
        focus = QApplication.focusWidget()
        editing_tempo = focus is self.tempo or (focus is not None and self.tempo.isAncestorOf(focus))
        for index, shortcut in enumerate(self.pad_keys):
            shortcut.setEnabled(self.waveform.slice_mode and not editing_tempo and offset + index < count)

    def trigger_pad(self, index):
        target = self.pad_page * len(self.pads) + index
        if self.waveform.slice_mode and target < len(self.waveform.slice_regions()):
            self.waveform.select_slice(target)
            self.play_slice()

    def show_beat_controls(self):
        self.beat_controls.show()
        self.find_tempo_button.show()
        self.waveform.show_first_beat = True
        self.waveform.setToolTip(tr('Drag the first-beat handle · Click the top strip to hear an interval'))
        self.update_beat_grid()
        self.tempo.setFocus()

    def hide_beat_controls(self):
        self.beat_anchor.setChecked(False)
        self.beat_controls.hide()
        self.find_tempo_button.hide()
        self.waveform.show_first_beat = False
        self.waveform.beat_drag_offset = None
        self.waveform.update()

    def place_first_beat(self, enabled):
        self.waveform.placing_first_beat = enabled
        self.waveform.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)
        if enabled:
            self.stop()
            self.status.setText(tr('Click the waveform to set the first beat'))
        else:
            self.status.clear()

    def update_first_beat(self, frame):
        self.beat_anchor.setChecked(False)
        wave = self.waveform
        seconds = frame / len(wave.frame_peaks) * wave.duration if wave.peaks else 0
        self.beat_anchor.setText(tr('First beat · {seconds:.3f} s').format(seconds=seconds))
        self.update_beat_grid()

    def update_beat_grid(self, *_):
        self.waveform.beat_interval = 60 * self.beat_division.currentData() / self.tempo.value()
        self.waveform.update()

    def find_tempo(self):
        self.stop()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            estimate = estimate_beat_grid(self.sample['path'], tempo_hint=self.tempo_hint)
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, tr('Could not find tempo'), str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        if estimate is None:
            self.status.setText(tr('No steady tempo found. Set BPM and first beat manually.'))
            return
        bpm, first_beat = estimate
        self.tempo.setValue(bpm)
        self.waveform.first_beat = first_beat
        self.waveform.beat_preview_index = 0
        self.update_first_beat(first_beat)
        self.status.setText(tr('Check the proposed grid before slicing.'))


    def slice_by_beat(self):
        self.beat_anchor.setChecked(False)
        wave = self.waveform
        if not wave.duration:
            return
        frames = len(wave.frame_peaks)
        first = wave.first_beat or 0
        markers = beat_markers(frames, round(frames / wave.duration), first,
                               self.tempo.value(), self.beat_division.currentData())
        if not markers:
            self.status.setText(tr('The audio is shorter than the selected beat interval.'))
            return
        # Keep the lead-in available on its own pad without selecting it for export.
        self.apply_auto_slices(markers, excluded={0} if first else (), index=int(first > 0))

    def slice_evenly(self, count):
        frames = len(self.waveform.frame_peaks)
        count = min(count, frames)
        if count > 1:
            self.apply_auto_slices([frames * index // count for index in range(1, count)])

    def find_attacks(self, sensitivity="balanced"):
        self.stop()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            markers = transient_markers(self.sample['path'], sensitivity=sensitivity)
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, tr('Could not analyze attacks'), str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        if markers:
            self.apply_auto_slices(markers)
        else:
            self.status.setText(tr('No distinct attacks found. Try equal slices or add markers on the waveform.'))

    def apply_auto_slices(self, markers, excluded=(), index=0):
        self.stop()
        wave = self.waveform
        self.previous_slices = (wave.slice_markers[:], wave.slice_excluded.copy(), wave.slice_index)
        wave.set_slices(markers, excluded, index)
        self.status.clear()

    def undo_auto_slice(self):
        if self.previous_slices is not None:
            self.stop()
            previous = self.previous_slices
            self.previous_slices = None
            self.waveform.set_slices(*previous)

    def update_slices(self):
        if not self.waveform.slice_mode:
            return
        regions = self.waveform.slice_regions()
        index = self.waveform.slice_index
        self.slice_included.setEnabled(bool(regions))
        with QSignalBlocker(self.slice_included):
            self.slice_included.setChecked(index not in self.waveform.slice_excluded)
        count = len(regions) - len(self.waveform.slice_excluded)
        self.add_slices.setText(tr('Add to Library ({count})').format(count=count))
        self.add_slices.setEnabled(len(regions) > 1 and count > 0)
        self.send_slices_button.setEnabled(self.add_slices.isEnabled())
        self.undo_auto.setEnabled(self.previous_slices is not None)
        offset = self.pad_page * len(self.pads)
        self.pad_back.setEnabled(offset > 0)
        self.pad_next.setEnabled(offset + len(self.pads) < len(regions))
        self.pad_range.setText(f'{offset + 1}–{min(offset + len(self.pads), len(regions))}' if regions else '—')
        for position, (pad, shortcut) in enumerate(zip(self.pads, self.pad_keys)):
            number = offset + position
            available = number < len(regions)
            pad.setText(f'{number + 1:02d}' if available else '—')
            pad.setAccessibleName(tr('Fragment {index} · {key}').format(index=number + 1, key=shortcut.key().toString()))
            self.pad_key_labels[position].setVisible(available)
            pad.setEnabled(available)
            pad.setChecked(available and number == index)
            pad.setToolTip(pad.accessibleName() + (' · ' + tr('Excluded from export') if number in self.waveform.slice_excluded else ''))
            pad.setProperty('excluded', number in self.waveform.slice_excluded)
            pad.style().unpolish(pad)
            pad.style().polish(pad)
        self.update_pad_shortcuts()

        if regions:
            start, end = regions[index]
            total = len(self.waveform.frame_peaks)
            duration = (end - start) / total * self.waveform.duration
            self.selection_label.setText(tr('Fragment {index} of {count} · {seconds:.3f} s').format(
                index=index + 1, count=len(regions), seconds=duration,
            ))
        else:
            self.selection_label.clear()

    def play_slice(self):
        regions = self.waveform.slice_regions()
        if not self.waveform.slice_mode or not regions:
            return
        self.play_frame_interval(*regions[self.waveform.slice_index])

    def play_beat_interval(self):
        region = self.waveform.beat_preview_region()
        if region is not None:
            self.play_frame_interval(*region)

    def play_frame_interval(self, start, end):
        source = self.sample['path']
        target = Path(self.temp.name) / 'slice-preview.wav'
        self.stop()
        try:
            total = len(self.waveform.frame_peaks)
            if round(total / self.waveform.duration) == 31250:
                copy_region_frames(source, target, start, end)
                self.play_path(target, start / total, end / total)
            else:
                self.player.play_region(source, start, end)
                self.start_playhead(start / total, end / total)
        except Exception as exc:
            QMessageBox.warning(self, tr('Playback error'), str(exc))

    def prepared_slices(self, folder):
        source = Path(self.sample['path'])
        name = Path(self.sample['name']).stem
        for index, (start, end) in enumerate(self.waveform.slice_regions()):
            if index in self.waveform.slice_excluded:
                continue
            target = Path(folder) / f'{index + 1:02d}.wav'
            copy_region_frames(source, target, start, end)
            yield source, target, name, index + 1, end - start

    def send_slices(self):
        self.stop()
        if self.send_fragments is None or not self.send_slices_button.isEnabled():
            return
        wave = self.waveform
        rate = round(len(wave.frame_peaks) / wave.duration)
        try:
            with TemporaryDirectory(dir=self.temp.name) as folder:
                fragments = [
                    dict(path=str(target), name=f'{name[:21]} {index:02d}',
                         duration=frames / rate)
                    for _, target, name, index, frames in self.prepared_slices(folder)
                ]
                self.send_fragments(fragments)
        except Exception as exc:
            QMessageBox.warning(self, tr('Could not send fragments'), str(exc))

    def save_slices(self):
        self.stop()
        try:
            with TemporaryDirectory(dir=self.temp.name) as folder:
                batch = [(source, target, f'{name} {index:02d}.wav')
                         for source, target, name, index, _ in self.prepared_slices(folder)]
                self.library.add_batch(batch)
        except Exception as exc:
            QMessageBox.warning(self, tr('Could not add fragments'), str(exc))
            return
        self.previous_slices = None
        self.waveform.reset_slices()
        self.status.setText(tr('Fragments added to library: {count}').format(count=len(batch)))

    def save_fades(self):
        self.stop()
        w = self.waveform
        view = (w.view_start, w.view_end)
        selection = w.get_selection()
        try:
            path = Path(self.temp.name) / 'fades.wav'
            apply_fades(self.fade_base, path, *w.fades)
            self.library.apply_edit(self.sample['id'], path)
            self.saved_fades = list(w.fades)
            w.load_file(self.sample['path'])
            w.set_view(*view)
            w.selection_start, w.selection_end = selection
            self.buttons['Undo'].setEnabled(True)
            self.status.setText(f'Fade-in: {w.fades[0]*self.sample["duration"]:.3f} s · Fade-out: {w.fades[1]*self.sample["duration"]:.3f} s · Saved')
        except Exception as exc:
            w.fades = list(self.saved_fades)
            w.update()
            QMessageBox.warning(self, tr('Fade was not saved'), str(exc))

    def reload(self):
        self.fade_base = self.sample['path']
        self.saved_fades = [0.0, 0.0, 1.0, 1.0]
        self.waveform.fades = list(self.saved_fades)
        self.waveform.load_file(self.sample['path'])
        self.waveform.fade_base_peaks = self.waveform.frame_peaks
        self.waveform.first_beat = None
        self.waveform.beat_preview_index = 0
        self.update_first_beat(0)
        self.previous_slices = None
        self.waveform.reset_slices()
        self.waveform.selection_start = self.waveform.selection_end = 0
        self.waveform.update()
        self.selection_changed(0, 0)
        self.buttons['Undo'].setEnabled(bool(self.sample.get('edit_history')))
        self.status.setText(f"{self.sample['duration']:.3f} s · Auto-save · Original retained")

    def selection_changed(self, start, end):
        duration = self.sample['duration']
        if self.waveform.fade_mode:
            selection = tr('Drag the handles to adjust fades')
        elif end > start:
            selection = f'Selection: {start * duration:.3f}–{end * duration:.3f} s · {(end - start) * duration:.3f} s'
        else:
            selection = tr('Drag on the waveform to select a region')
        self.trim_label.setText(selection)
        valid = end > start and not self.waveform.fade_mode
        self.buttons['▶ Selection'].setEnabled(valid)
        self.buttons['Keep selection'].setEnabled(valid and (start > 0 or end < 1))
        self.buttons['Delete selection'].setEnabled(valid and (start > 0 or end < 1))

    def play_path(self, path, start=0, end=1):
        self.stop()
        try:
            self.player.play(str(path))
            self.start_playhead(start, end)
        except Exception as exc:
            QMessageBox.warning(self, tr('Playback error'), str(exc))

    def start_playhead(self, start, end):
        self.play_start, self.play_end = start, end
        self.waveform.set_playhead(start)
        self.started = monotonic()
        self.play_button.setText(tr("■ Stop"))
        self.timer.start()

    def toggle_playback(self):
        if self.timer.isActive():
            self.stop()
        else:
            if self.waveform.slice_mode:
                self.play_slice()
            else:
                self.play()

    def play(self):
        self.play_path(self.sample['path'])

    def play_selection(self):
        start, end = self.waveform.get_selection()
        try:
            path = Path(self.temp.name) / 'selection.wav'
            self.stop()
            cut_region(self.sample['path'], path, start, end, keep=True)
            self.play_path(path, start, end)
        except Exception as exc:
            QMessageBox.warning(self, tr('Selection error'), str(exc))

    def tick(self):
        if not self.player.is_playing():
            self.stop()
            return
        self.waveform.set_playhead(min(self.play_end, self.play_start + (monotonic()-self.started)/self.sample['duration']))

    def stop(self):
        self.timer.stop()
        self.slice_preview_timer.stop()
        self.player.stop()
        self.waveform.set_playhead(0)
        self.play_button.setText(tr("▶ Play"))

    def edit(self, keep):
        self.stop()
        try:
            start, end = self.waveform.get_selection()
            path = Path(self.temp.name) / 'edited.wav'
            cut_region(self.sample['path'], path, start, end, keep)
            self.library.apply_edit(self.sample['id'], path)
            self.reload()
            self.status.setText(f"Saved · {self.sample['duration']:.3f} s · Undo available")
            self.play()
        except Exception as exc:
            QMessageBox.warning(self, tr('Edit was not saved'), str(exc))

    def undo(self):
        self.stop()
        try:
            self.library.undo_edit(self.sample['id']); self.reload()
        except Exception as exc:
            QMessageBox.warning(self, tr('Undo failed'), str(exc))

    def done(self, result):
        self.stop()
        super().done(result)

    def cleanup(self):
        self.stop(); self.temp.cleanup()
