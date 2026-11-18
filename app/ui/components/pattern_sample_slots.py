from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.device.pattern_steps import read_steps, remap_sample_slots
from app.ui.components.transfer_preview import TransferPreviewTable
from app.ui.transfer_history import suggested_transfers
from app.ui.translations import tr


AUDIO_LABELS = {"matches": "Matches snapshot", "different": "Different sample", "empty": "Empty"}


class PatternSampleSlotsDialog(QDialog):
    def __init__(self, pattern, device_slots, parent=None, *, audio_status=None, prepare_samples=None):
        super().__init__(parent)
        self.pattern = pattern
        self.slots = {number: name for number, name, duration in device_slots if duration is not None}
        self.targets = {}
        _, parts = read_steps(pattern)
        sources = sorted({slot for slot, _ in parts})
        self.setWindowTitle(tr("Review pattern sample slots"))
        self.resize(640, 450)
        layout = QVBoxLayout(self)
        note = QLabel(tr("Choose replacement slots for this restore. Slot occupancy does not verify the sound."))
        note.setWordWrap(True)
        layout.addWidget(note)
        audio_status = audio_status or {}

        def source_label(slot):
            if slot not in self.slots or audio_status.get(slot) == "empty":
                return tr("Empty")
            name = self.slots[slot] or tr("Unnamed sample")
            status = audio_status.get(slot)
            return f"{name} · {tr(AUDIO_LABELS[status])}" if status else name

        table = TransferPreviewTable([(slot, source_label(slot), "") for slot in sources])
        table.setHorizontalHeaderLabels([tr("Slot"), tr("On Volca"), tr("Destination slot")])
        table.verticalHeader().setDefaultSectionSize(38)
        layout.addWidget(table, 1)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        layout.addWidget(self.warning)
        for row, source in enumerate(sources):
            target = QComboBox()
            target.addItem(f"{source:03d} · " + tr("Keep slot"), source)
            for number, name in sorted(self.slots.items()):
                if number != source:
                    target.addItem(f"{number:03d} · {name or tr('Unnamed sample')}", number)
            target.setAccessibleName(tr("Destination for slot {slot:03d}").format(slot=source))
            target.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            target.currentIndexChanged.connect(self._update_warning)
            table.setCellWidget(row, 2, target)
            self.targets[source] = target
            if source not in self.slots:
                table.item(row, 1).setForeground(self.palette().link())
        buttons = QHBoxLayout()
        suggestions = {source: target for source, target in suggested_transfers(device_slots).items()
                       if source in self.targets}

        def apply_suggestions():
            for source, destination in suggestions.items():
                combo = self.targets[source]
                combo.setCurrentIndex(combo.findData(destination))

        if suggestions:
            use_suggestions = QPushButton(tr("Use last set transfer"))
            use_suggestions.setToolTip(tr("Apply suggested slots from the last confirmed set transfer."))
            use_suggestions.clicked.connect(apply_suggestions)
            buttons.addWidget(use_suggestions)
        if prepare_samples is not None:
            prepare = QPushButton(tr("Prepare pattern samples…"))

            def prepare_missing():
                confirmed = prepare_samples(self.destinations())
                for source, (destination, name) in confirmed.items():
                    self.slots[destination] = name
                    for combo in self.targets.values():
                        if combo.findData(destination) < 0:
                            combo.addItem(f"{destination:03d} · {name}", destination)
                    combo = self.targets[source]
                    combo.setCurrentIndex(combo.findData(destination))
                    if source == destination:
                        item = table.item(sources.index(source), 1)
                        item.setText(source_label(source))
                        item.setToolTip(item.text())
                        item.setForeground(self.palette().text())
                self._update_warning()

            prepare.clicked.connect(prepare_missing)
            layout.addWidget(prepare, alignment=Qt.AlignLeft)
        buttons.addStretch()
        cancel = QPushButton(tr("Cancel"))
        cancel.clicked.connect(self.reject)
        continue_button = QPushButton(tr("Continue…"))
        continue_button.setObjectName("primaryAction")
        continue_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(continue_button)
        layout.addLayout(buttons)
        self._update_warning()

    def _update_warning(self):
        empty = self.empty_slots()
        slots = ", ".join(f"{number:03d}" for number in empty)
        self.warning.setText(tr("Empty destination slots: {slots}").format(slots=slots) if empty else "")

    def empty_slots(self):
        return sorted({target.currentData() for target in self.targets.values()} - self.slots.keys())

    def destinations(self):
        return {source: target.currentData() for source, target in self.targets.items()}

    def mapping(self):
        return {source: target for source, target in self.destinations().items() if target != source}

    def replacement(self):
        return remap_sample_slots(self.pattern, self.mapping())
