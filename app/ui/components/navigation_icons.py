from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


def navigation_icon(section, *, light=False):
    icon = QIcon()
    inactive, active = ("#73626a", "#724452") if light else ("#b4a8aa", "#edc1c4")
    for state, color in ((QIcon.Off, inactive), (QIcon.On, active)):
        pixmap = QPixmap(36, 36)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(color), 1.35, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if section == "device":
            painter.drawRoundedRect(QRectF(1.75, 3.75, 14.5, 10.5), 2, 2)
            painter.drawLine(QPointF(4.5, 7.25), QPointF(8, 7.25))
            for x in (5, 9, 13):
                painter.drawPoint(QPointF(x, 11.25))
        elif section == "samples":
            for x, height in ((2.5, 3), (5.75, 7), (9, 12), (12.25, 8), (15.5, 4)):
                painter.drawLine(QPointF(x, 9 - height / 2), QPointF(x, 9 + height / 2))
        elif section == "patterns":
            for y in (3.75, 10.25):
                for x in (2, 7.25, 12.5):
                    painter.drawRoundedRect(QRectF(x, y, 3.5, 4), 0.8, 0.8)
        elif section == "theme":
            painter.drawEllipse(QRectF(2, 2, 14, 14))
            painter.setBrush(QColor(color))
            painter.drawChord(QRectF(2, 2, 14, 14), 90 * 16, 180 * 16)
        elif section == "settings":
            for y, x in ((4, 6), (9, 12), (14, 8)):
                painter.drawLine(QPointF(2, y), QPointF(x - 2, y))
                painter.drawEllipse(QPointF(x, y), 2, 2)
                painter.drawLine(QPointF(x + 2, y), QPointF(16, y))
        painter.end()
        icon.addPixmap(pixmap, QIcon.Normal, state)
    return icon
