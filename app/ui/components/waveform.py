import array
import sys
import wave
import numpy as np

from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class WaveformWidget(QWidget):
    selection_changed = Signal(float, float)
    view_changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.peaks = []
        self.frame_peaks = np.array([])
        self.view_start, self.view_end = 0.0, 1.0
        self.duration = 0.0
        self.playhead_position = 0.0

        self.selection_start = 0.0
        self.selection_end = 1.0
        self.dragging = False

        self.setMinimumHeight(160)
        self.setMouseTracking(True)
        self.setToolTip("Two-finger scroll / wheel: pan · Pinch or ⌘/Ctrl + wheel: zoom at pointer")

    def load_file(self, path):
        self.peaks = []
        self.frame_peaks = np.array([])
        self.view_start, self.view_end = 0.0, 1.0
        self.duration = 0.0
        self.playhead_position = 0.0
        self.selection_start = 0.0
        self.selection_end = 1.0

        try:
            with wave.open(path, "rb") as audio:
                channels = audio.getnchannels()
                sample_width = audio.getsampwidth()
                frames = audio.getnframes()

                if sample_width != 2 or channels < 1:
                    self.update()
                    return

                raw = audio.readframes(frames)
                data = np.frombuffer(raw, dtype='<i2').reshape(-1, channels)
                self.frame_peaks = np.max(np.abs(data.astype(np.int32)), axis=1) / 32768.0 if len(data) else np.array([])
                self.peaks = [1] if len(data) else []
                self.duration = frames / audio.getframerate()

        except (wave.Error, OSError, ValueError, IndexError):
            self.peaks = []

        self.view_changed.emit(0.0, 1.0)
        self.update()

    def set_view(self, start, end):
        span = max(1 / 64, min(1.0, end - start))
        self.view_start = max(0.0, min(1.0 - span, start))
        self.view_end = self.view_start + span
        self.view_changed.emit(self.view_start, self.view_end)
        self.update()

    def zoom(self, factor):
        span = (self.view_end - self.view_start) / factor
        start, end = self.get_selection()
        center = (start + end) / 2 if end > start else (self.view_start + self.view_end) / 2
        span = max(1 / 64, min(1.0, span))
        self.set_view(center - span / 2, center + span / 2)

    def zoom_at(self, factor, x):
        fraction = max(0.0, min(1.0, x / max(1, self.width())))
        old_span = self.view_end - self.view_start
        anchor = self.view_start + fraction * old_span
        span = max(1 / 64, min(1.0, old_span / max(0.01, factor)))
        self.set_view(anchor - fraction * span, anchor + (1 - fraction) * span)

    def wheelEvent(self, event):
        if not self.peaks or self.dragging:
            event.ignore()
            return
        pixels = event.pixelDelta()
        angles = event.angleDelta()
        use_pixels = not pixels.isNull()
        delta = pixels if use_pixels else angles
        amount = delta.x() if abs(delta.x()) > abs(delta.y()) else delta.y()
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            steps = amount / (240 if use_pixels else 120)
            self.zoom_at(2 ** max(-4, min(4, steps / 3)), event.position().x())
        else:
            span = self.view_end - self.view_start
            offset = -amount / max(1, self.width()) * span if use_pixels else -amount / 120 * span * 0.1
            self.set_view(self.view_start + offset, self.view_end + offset)
        event.accept()

    def event(self, event):
        if event.type() == QEvent.NativeGesture and self.peaks and not self.dragging:
            if event.gestureType() == Qt.ZoomNativeGesture:
                self.zoom_at(max(0.01, 1 + event.value()), event.position().x())
                event.accept()
                return True
            if event.gestureType() in (Qt.BeginNativeGesture, Qt.EndNativeGesture):
                event.accept()
                return True
        return super().event(event)

    def set_playhead(self, position):
        self.playhead_position = max(
            0.0, min(1.0, position)
        )
        self.update()

    def _mouse_position(self, event):
        return max(
            0.0,
            min(1.0, self.view_start + (self.view_end - self.view_start) * event.position().x() / max(1, self.width()))
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.peaks:
            position = self._mouse_position(event)
            self.selection_start = position
            self.selection_end = position
            self.dragging = True
            self.update()

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.selection_end = self._mouse_position(event)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.dragging:
            self.selection_end = self._mouse_position(event)
            self.dragging = False

            start, end = self.get_selection()
            self.selection_changed.emit(start, end)
            self.update()

    def get_selection(self):
        return (
            min(self.selection_start, self.selection_end),
            max(self.selection_start, self.selection_end)
        )

    def visible_peaks(self, first, last):
        return self.frame_peaks[first:last]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.fillRect(self.rect(), QColor("#191919"))

        if not self.peaks:
            painter.setPen(QColor("#a6a6a6"))
            painter.drawText(
                self.rect(),
                Qt.AlignCenter,
                "Select a 16-bit PCM WAV sample"
            )
            return

        width = self.width()
        height = self.height()
        center = (height - 22) / 2
        span = self.view_end - self.view_start
        to_x = lambda position: int((position - self.view_start) / span * width)

        start, end = self.get_selection()

        if end > start:
            x1 = max(0, to_x(start))
            x2 = min(width, to_x(end))

            painter.fillRect(
                x1, 0, x2 - x1, height,
                QColor(166, 104, 108, 65)
            )

        painter.setPen(QPen(QColor("#b98286"), 1.5))

        total = len(self.frame_peaks)
        offset = min(total - 1, int(self.view_start * total))
        limit = min(total, max(offset + 1, int((self.view_start + span) * total)))
        peaks = self.visible_peaks(offset, limit)
        for x in range(width):
            first = min(total - 1, int((self.view_start + span * x / width) * total))
            last = min(limit, max(first + 1, int((self.view_start + span * (x + 1) / width) * total)))
            peak = float(peaks[first - offset:last - offset].max())
            amplitude = peak * (height - 24) * 0.43
            painter.drawLine(x, int(center - amplitude), x, int(center + amplitude))
        painter.setPen(QColor("#aaaaaa"))
        for tick in range(5):
            position = self.view_start + span * tick / 4
            x = min(width - 65, max(0, int(tick * width / 4)))
            painter.drawText(x, height - 5, f"{position * self.duration:.3f}s")

        painter.setPen(QPen(QColor("#ececec"), 2))
        x = to_x(self.playhead_position)
        painter.drawLine(x, 0, x, height - 1)

        if end > start:
            painter.setPen(QPen(QColor("#e0b1b4"), 1))
            for position in (start, end):
                x = to_x(position)
                painter.drawLine(x, 0, x, height - 1)
