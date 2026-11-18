from PySide6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget


class _Faceplate(QWidget):
    """A stylized Volca Sample 2 with a display for the selected slot."""

    SCALE = 1.75
    SEGMENTS = (
        (1, 0, 3, 1), (4, 1, 1, 3), (4, 5, 1, 3),
        (1, 8, 3, 1), (0, 5, 1, 3), (0, 1, 1, 3), (1, 4, 3, 1),
    )
    DIGITS = (
        "012345", "12", "01346", "01236", "1256",
        "02356", "023456", "012", "0123456", "012356",
    )

    def __init__(self):
        super().__init__()
        self.setFixedSize(round(112 * self.SCALE), round(67 * self.SCALE))
        self.connected = False
        self.backlight = 0.0
        self.light_transition = QVariantAnimation(self)
        self.light_transition.setDuration(180)
        self.light_transition.setEasingCurve(QEasingCurve.OutCubic)
        self.light_transition.valueChanged.connect(self._set_backlight)
        self.set_slot(None)

    def set_connected(self, connected):
        if connected != self.connected:
            self.connected = connected
            self.light_transition.stop()
            target = float(connected)
            if self.isVisible():
                self.light_transition.setStartValue(self.backlight)
                self.light_transition.setEndValue(target)
                self.light_transition.start()
            else:
                self._set_backlight(target)

    def _set_backlight(self, intensity):
        self.backlight = intensity
        self.update()

    def _lit_color(self, off, on):
        dark, lit = QColor(off), QColor(on)
        return QColor.fromRgbF(
            dark.redF() + (lit.redF() - dark.redF()) * self.backlight,
            dark.greenF() + (lit.greenF() - dark.greenF()) * self.backlight,
            dark.blueF() + (lit.blueF() - dark.blueF()) * self.backlight,
        )

    def set_slot(self, slot):
        self.slot = slot
        self.setAccessibleName("Volca Sample 2 · No slot selected" if slot is None
                               else f"Volca Sample 2 · Selected slot {slot:03d}")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(self.SCALE, self.SCALE)
        painter.setPen(QColor("#4a4445"))
        painter.setBrush(QColor("#302c2d"))
        painter.drawRoundedRect(QRectF(0.5, 0.5, 111, 66), 7, 7)
        painter.setPen(Qt.NoPen)

        painter.setBrush(QColor("#262324"))
        painter.drawRoundedRect(QRectF(5, 14, 102, 35), 3, 3)
        painter.setBrush(QColor("#5b5354"))
        for x in (10, 65, 77, 88, 99):
            painter.drawEllipse(QRectF(x, 6, 5, 5))
        for x in (9, 21):
            painter.drawEllipse(QRectF(x, 21, 9, 9))

        painter.setBrush(QColor("#161415"))
        painter.drawRoundedRect(QRectF(7, 33, 26, 14), 2, 2)
        text = "---" if self.slot is None else f"{self.slot:03d}"
        segment_off = QColor("#292929")
        segment_on = self._lit_color("#292929", "#c37b80")
        for index, digit in enumerate(text):
            lit = "6" if digit == "-" else self.DIGITS[int(digit)]
            for segment, (x, y, width, height) in enumerate(self.SEGMENTS):
                painter.setBrush(segment_on if str(segment) in lit else segment_off)
                painter.drawRoundedRect(
                    QRectF(10.5 + 7 * index + x, 35.5 + y, width, height), 0.35, 0.35,
                )

        painter.setBrush(self._lit_color("#505050", "#a6686c"))
        for y in (19, 28, 37):
            for x in (38, 47, 56, 65):
                painter.drawEllipse(QRectF(x, y, 5, 5))
        painter.setBrush(QColor("#6b6061"))
        for y in (25, 35):
            for x in (77, 87, 97):
                painter.drawRoundedRect(QRectF(x, y, 6, 5), 1, 1)
        painter.setBrush(QColor("#50494a"))
        for step in range(16):
            painter.drawRoundedRect(QRectF(6 + step * 6.3, 53, 5, 8), 1, 1)
        if self.backlight:
            painter.setOpacity(self.backlight)
            painter.setBrush(QColor("#a6686c"))
            for step in range(16):
                painter.drawEllipse(QRectF(7.6 + step * 6.3, 54.4, 1.8, 1.8))


class VolcaPanel(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("panel")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 14, 10)
        layout.setSpacing(14)
        self.faceplate = _Faceplate()
        layout.addWidget(self.faceplate)

        details = QVBoxLayout()
        details.setSpacing(3)
        details.addStretch()
        title = QLabel("Volca Sample 2")
        title.setObjectName("devicePanelTitle")
        details.addWidget(title)
        self.sample = QLabel("USB MIDI")
        self.sample.setTextFormat(Qt.PlainText)
        self.sample.setObjectName("muted")
        self.sample.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        details.addWidget(self.sample)
        details.addStretch()
        layout.addLayout(details, 1)

    def set_connected(self, connected):
        self.faceplate.set_connected(connected)

    def set_selection(self, slot=None, name=""):
        self.faceplate.set_slot(slot)
        self.sample.setText(name or "USB MIDI")
        self.sample.setToolTip(name)
