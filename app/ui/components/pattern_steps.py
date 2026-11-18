from app.ui.translations import tr
from PySide6.QtCore import QRectF, QSignalBlocker, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDial, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from app.device.pattern_steps import (
    STEP_COUNT, read_part_sound, read_step_motion, read_steps, write_active_steps, write_part_samples, write_step_motion, write_steps,
)


class StepKnob(QDial):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = self.rect().center()
        arc = QRectF(center.x() - 15, center.y() - 15, 30, 30)
        painter.setPen(QPen(self.palette().mid().color(), 3, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(arc, 225 * 16, -270 * 16)
        fraction = (self.value() - self.minimum()) / (self.maximum() - self.minimum())
        accent = QColor("#d6a0a4" if self.hasFocus() else "#a6686c")
        if self.isEnabled():
            painter.setPen(QPen(accent, 3, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(arc, 225 * 16, round(-270 * 16 * fraction))
        painter.save()
        painter.translate(arc.center())
        painter.rotate(-135 + fraction * 270)
        painter.setPen(QPen(self.palette().text().color(), 2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(0, -9, 0, -4)
        painter.restore()


class PatternStepButton(QPushButton):
    def __init__(self):
        super().__init__()
        self.has_motion = False

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.has_motion:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            triggered = self.isChecked() and not self.property("skipped")
            light = self.window().palette().window().color().lightness() >= 128
            if light:
                color = "#733440" if triggered else "#8b4856"
            else:
                color = "#ffe5e7" if triggered else "#c5868d"
            painter.setBrush(QColor(color))
            painter.drawEllipse(QRectF(self.width() - 9, 4, 5, 5))


class PatternStepsDialog(QDialog):
    def __init__(self, slot, name, data, editable, parent=None, *, sample_slots=None):
        super().__init__(parent)
        active, parts = read_steps(data)
        self.data = data
        self.original_active = self.active = active
        self.step_buttons = [[] for _ in range(STEP_COUNT)]
        self.original_masks = [mask for _, mask in parts]
        self.masks = self.original_masks.copy()
        self.original_samples = [sample for sample, _ in parts]
        self.samples = self.original_samples.copy()
        self.base_sound = read_part_sound(data)
        self.motion = {}
        self.selected_step = 0
        self.editable = editable
        names = sample_slots or {}
        self.setWindowTitle(tr("Pattern steps"))
        layout = QVBoxLayout(self)
        title = QLabel(f"{slot:02d} · {name or 'Unnamed pattern'}")
        title.setTextFormat(Qt.PlainText)
        title.setObjectName("section")
        layout.addWidget(title)
        hint = QLabel(tr("Right-click a step to open its sound controls." if editable else
                         "Click a part number to view its step controls."))
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.selected_part = 0
        self.part_buttons = []

        grid = QGridLayout()
        grid.setSpacing(4)
        step_columns = [step + 1 + step // 4 for step in range(STEP_COUNT)]
        for start in range(4, STEP_COUNT, 4):
            grid.setColumnMinimumWidth(step_columns[start] - 1, 6)
        part_heading = QLabel(tr("Part / Slot"))
        part_heading.setObjectName("muted")
        grid.addWidget(part_heading, 0, 0)
        grid.addWidget(QLabel(tr("Active Step")), 1, 0)
        for step in range(STEP_COUNT):
            label = QLabel(str(step + 1))
            label.setAlignment(Qt.AlignCenter)
            grid.addWidget(label, 0, step_columns[step])
            toggle = QPushButton()
            toggle.setObjectName("activeStep")
            toggle.setFixedSize(28, 17)
            toggle.setCheckable(True)
            toggle.setChecked(bool(active & (1 << step)))
            toggle.setEnabled(editable)
            toggle.setAutoDefault(False)
            toggle.setAccessibleName(tr("Active step {step}").format(step=step + 1))
            toggle.setToolTip(tr("Include this step in the pattern"))
            toggle.toggled.connect(lambda checked, index=step: self._toggle_active(index, checked))
            grid.addWidget(toggle, 1, step_columns[step])
        for part, (sample, mask) in enumerate(parts):
            part_row = QHBoxLayout()
            select_part = QPushButton(f"{part + 1:02d}")
            select_part.setObjectName("patternPart")
            select_part.setCheckable(True)
            select_part.setAutoDefault(False)
            select_part.setFixedSize(30, 25)
            caption = tr("Step controls for part {part}").format(part=part + 1)
            select_part.setAccessibleName(caption)
            select_part.setToolTip(caption)
            select_part.clicked.connect(lambda checked, index=part: self._select_sound_part(index, checked))
            self.part_buttons.append(select_part)
            part_row.addWidget(select_part)
            if editable and names:
                picker = QComboBox()
                picker.setObjectName("patternSamplePicker")
                picker.setProperty("settingsChoice", True)
                picker.setAccessibleName(tr("Sample for part {part}").format(part=part + 1))
                picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                picker.setMinimumContentsLength(14)
                picker.setMaximumWidth(235)
                for number in sorted(names.keys() | {sample}):
                    picker.addItem(self._sample_label(number, names), number)
                picker.setCurrentIndex(picker.findData(sample))
                picker.setToolTip(picker.currentText())
                picker.currentIndexChanged.connect(
                    lambda _, control=picker, p=part: self._select_sample(p, control)
                )
                part_row.addWidget(picker, 1)
            else:
                label = QLabel(self._sample_label(sample, names))
                label.setTextFormat(Qt.PlainText)
                label.setWordWrap(True)
                label.setMaximumWidth(235)
                part_row.addWidget(label, 1)
            grid.addLayout(part_row, part + 2, 0)
            for step in range(STEP_COUNT):
                button = PatternStepButton()
                button.setObjectName("patternStep")
                button.setFixedSize(28, 25)
                button.setCheckable(True)
                button.setChecked(bool(mask & (1 << step)))
                skipped = not bool(active & (1 << step))
                button.setProperty("skipped", skipped)
                self.step_buttons[step].append(button)
                self._update_step_marker(part, step)
                button.setAccessibleName(tr("Part {part} · Step {step}").format(part=part + 1, step=step + 1))
                button.setEnabled(editable)
                button.setAutoDefault(False)
                button.toggled.connect(lambda checked, p=part, s=step: self._toggle(p, s, checked))
                button.setContextMenuPolicy(Qt.CustomContextMenu)
                button.customContextMenuRequested.connect(lambda _, p=part, s=step: self._select_step(p, s))
                grid.addWidget(button, part + 2, step_columns[step])
        layout.addLayout(grid)
        self.sound_panel = QFrame()
        self.sound_panel.setObjectName("patternSoundPanel")
        self.sound_panel.setLayout(self._sound_controls())
        self.sound_panel.hide()
        layout.addWidget(self.sound_panel)

        footer = QHBoxLayout()
        if not editable:
            mode = QLabel(tr("Read-only"))
            mode.setObjectName("muted")
            footer.addWidget(mode)
        footer.addStretch()
        close = QPushButton(tr("Cancel") if editable else tr("Close"))
        close.setAutoDefault(False)
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        self.save = QPushButton(tr("Save to Volca"))
        self.save.setObjectName("primaryAction")
        self.save.setAutoDefault(False)
        self.save.setEnabled(False)
        self.save.clicked.connect(self.accept)
        if editable:
            footer.addWidget(self.save)
        layout.addLayout(footer)

    def _sound_controls(self):
        row = QHBoxLayout()
        row.setContentsMargins(10, 6, 10, 6)
        selection = QVBoxLayout()
        self.sound_title = QLabel()
        self.sound_title.setObjectName("section")
        selection.addWidget(self.sound_title)
        self.step_picker = QComboBox()
        self.step_picker.setAccessibleName(tr("Step"))
        self.step_picker.addItems([f"{step + 1:02d}" for step in range(STEP_COUNT)])
        self.step_picker.currentIndexChanged.connect(self._pick_step)
        selection.addWidget(self.step_picker)
        row.addLayout(selection)
        row.addStretch()
        self.knobs = []
        self.knob_values = []
        for index, caption in enumerate(("Level", "Pan", "Speed")):
            column = QVBoxLayout()
            column.setSpacing(0)
            knob = StepKnob()
            knob.setFixedSize(44, 40)
            knob.setRange(-63 if index == 1 else 0, 63 if index == 1 else 127)
            knob.setAccessibleName(tr(caption))
            knob.setEnabled(self.editable)
            knob.valueChanged.connect(lambda value, parameter=index: self._set_motion(parameter, value))
            value = QLabel()
            column.addWidget(QLabel(tr(caption)), alignment=Qt.AlignCenter)
            column.addWidget(knob, alignment=Qt.AlignCenter)
            column.addWidget(value, alignment=Qt.AlignCenter)
            row.addLayout(column)
            self.knobs.append(knob)
            self.knob_values.append(value)
        row.addStretch()
        self.reset_sound = QPushButton(tr("Reset step controls"))
        self.reset_sound.setAutoDefault(False)
        self.reset_sound.clicked.connect(self._reset_sound)
        row.addWidget(self.reset_sound)
        self._show_sound()
        return row

    def _select_sound_part(self, part, checked):
        if checked:
            self._select_step(part, self.selected_step)
        else:
            self._mark_selected(self.selected_part, self.selected_step, False)
            self.sound_panel.hide()
            self.adjustSize()

    def _select_step(self, part, step):
        self._mark_selected(self.selected_part, self.selected_step, False)
        self.selected_part, self.selected_step = part, step
        self._mark_selected(part, step, True)
        for index, button in enumerate(self.part_buttons):
            button.setChecked(index == part)
        self._show_sound()
        if self.sound_panel.isHidden():
            self.sound_panel.show()
            self.adjustSize()

    def _mark_selected(self, part, step, selected):
        button = self.step_buttons[step][part]
        button.setProperty("selectedStep", selected)
        self._refresh_step_style(button)

    @staticmethod
    def _refresh_step_style(button):
        style = button.style()
        style.unpolish(button)
        style.polish(button)
        button.update()

    def _update_step_marker(self, part, step):
        button = self.step_buttons[step][part]
        saved = read_step_motion(self.data, part, step)
        button.has_motion = any(
            (part, step, parameter) in self.motion or any(points)
            for parameter, points in enumerate(saved)
        )
        details = []
        if button.has_motion:
            details.append(tr("Motion recorded"))
        if button.property("skipped"):
            details.append(tr("Skipped by Active Step"))
        description = " · ".join(details)
        button.setToolTip(description)
        button.setAccessibleDescription(description)
        button.update()

    def _pick_step(self, step):
        self._select_step(self.selected_part, step)

    def _motion_key(self, parameter):
        return self.selected_part, self.selected_step, parameter

    def _reset_sound(self):
        for parameter in range(len(self.knobs)):
            self.motion.pop(self._motion_key(parameter), None)
        self._update_step_marker(self.selected_part, self.selected_step)
        self._show_sound()
        self._update_save()

    def _show_sound(self):
        self.sound_title.setText(tr("Part {part} · Step {step}").format(
            part=f"{self.selected_part + 1:02d}", step=f"{self.selected_step + 1:02d}",
        ))
        with QSignalBlocker(self.step_picker):
            self.step_picker.setCurrentIndex(self.selected_step)
        points = read_step_motion(self.data, self.selected_part, self.selected_step)
        level, pan, speed = self.base_sound[self.selected_part]
        defaults = (level, pan, speed - 128 if speed >= 128 else 64)
        for index, knob in enumerate(self.knobs):
            override = self.motion.get(self._motion_key(index))
            first = points[index][0]
            value = override if override is not None else first - 128 if first >= 128 else defaults[index]
            with QSignalBlocker(knob):
                knob.setValue(value - 64 if index == 1 else value)
            if override is None and index == 2 and 0 < first < 128:
                self.knob_values[index].setText(tr("Chromatic"))
            else:
                self.knob_values[index].setText(str(knob.value()))
        self.reset_sound.setEnabled(self.editable and any(self._motion_key(i) in self.motion for i in range(3)))

    def _set_motion(self, parameter, value):
        self.motion[self._motion_key(parameter)] = value + 64 if parameter == 1 else value
        self._update_step_marker(self.selected_part, self.selected_step)
        self.knob_values[parameter].setText(str(value))
        self.reset_sound.setEnabled(True)
        self._update_save()

    def _toggle(self, part, step, checked):
        bit = 1 << step
        self.masks[part] = (self.masks[part] | bit) if checked else (self.masks[part] & ~bit)
        if not self.sound_panel.isHidden():
            self._select_step(part, step)
        self._update_save()

    def _toggle_active(self, step, checked):
        bit = 1 << step
        self.active = self.active | bit if checked else self.active & ~bit
        for part, button in enumerate(self.step_buttons[step]):
            button.setProperty("skipped", not checked)
            self._update_step_marker(part, step)
            self._refresh_step_style(button)
        self._update_save()

    @staticmethod
    def _sample_label(number, names):
        return f"{number:03d} · {names[number] or tr('Unnamed sample')}" if number in names else f"{number:03d}"

    def _select_sample(self, part, picker):
        self.samples[part] = picker.currentData()
        picker.setToolTip(picker.currentText())
        self._update_save()

    def _update_save(self):
        self.save.setEnabled(
            self.active != self.original_active or self.masks != self.original_masks
            or self.samples != self.original_samples or bool(self.motion)
        )

    def replacement(self):
        data = write_active_steps(self.data, self.active)
        data = write_part_samples(write_steps(data, self.masks), self.samples)
        return write_step_motion(data, self.motion)
