from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QStyledItemDelegate

WAVEFORM_PATH = Qt.UserRole + 5
_WIDTH = 72
_HEIGHT = 22


@lru_cache(maxsize=256)
def _thumbnail(path, modified, size):
    try:
        with sf.SoundFile(path) as audio:
            if not audio.frames:
                return QIcon()
            peaks = []
            count = min(audio.frames, 36)
            for index in range(count):
                start = index * audio.frames // count
                end = (index + 1) * audio.frames // count
                audio.seek(start)
                frames = audio.read(end - start, dtype="float32", always_2d=True)
                peaks.append(float(np.abs(frames).max()) if frames.size else 0.0)
    except (OSError, RuntimeError, ValueError):
        return QIcon()

    pixmap = QPixmap(_WIDTH * 2, _HEIGHT * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setPen(QColor("#b4777c"))
    scale = max(max(peaks), 0.001)
    for index, peak in enumerate(peaks):
        height = max(1, round(9 * peak / scale))
        x = round((index + 0.5) * _WIDTH / count)
        painter.drawLine(x, 11 - height, x, 11 + height)
    painter.end()
    return QIcon(pixmap)


class SlotWaveformDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        path = index.data(WAVEFORM_PATH)
        if not path or 0 < option.rect.width() < 120:
            return
        try:
            stat = Path(path).stat()
        except OSError:
            return
        icon = _thumbnail(path, stat.st_mtime_ns, stat.st_size)
        if not icon.isNull():
            option.icon = icon
            option.decorationPosition = option.Position.Right
            width = 40 if 0 < option.rect.width() < 220 else _WIDTH
            option.decorationSize = QSize(width, _HEIGHT)
            option.features |= option.ViewItemFeature.HasDecoration
