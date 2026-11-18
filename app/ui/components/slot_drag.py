import json
from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QMimeData, Signal, QTimer
from PySide6.QtGui import QColor, QDrag, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QPushButton, QTableWidget, QToolTip
from app.ui.components.file_drop_list import FileDropList
from app.ui.translations import tr

MIME = 'application/x-volcastudio-sample-id'
SLOT_MIME = 'application/x-volcastudio-slot'
GROUP_MIME = 'application/x-volcastudio-sample-ids'


def _drag_preview(source, text):
    metrics = source.fontMetrics()
    text = metrics.elidedText(text, Qt.ElideRight, 235)
    width = metrics.horizontalAdvance(text) + 39
    height = 32
    scale = source.devicePixelRatioF()
    image = QPixmap(round(width * scale), round(height * scale))
    image.setDevicePixelRatio(scale)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#b77a82"), 1))
    painter.setBrush(QColor("#3e2d31"))
    painter.drawRoundedRect(QRectF(0.5, 0.5, width - 1, height - 1), 8, 8)
    painter.setPen(QPen(QColor("#e4a7b0"), 1.5, Qt.SolidLine, Qt.RoundCap))
    for index, bar in enumerate((4, 9, 13, 7, 4)):
        x = 11 + index * 3
        painter.drawLine(x, (height - bar) // 2, x, (height + bar) // 2)
    painter.setPen(QColor("#f6edf0"))
    painter.setFont(source.font())
    painter.drawText(QRectF(30, 0, width - 39, height), Qt.AlignVCenter | Qt.AlignLeft, text)
    painter.end()
    return image


def _start_drag(source, mime, action, text):
    drag = QDrag(source)
    drag.setMimeData(mime)
    drag.setPixmap(_drag_preview(source, text))
    drag.setHotSpot(QPoint(12, 16))
    drag.exec(action)


class LibraryDragList(FileDropList):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)

    def startDrag(self, actions):
        items = [self.item(row) for row in range(self.count())
                 if self.item(row).isSelected() and not self.item(row).isHidden()]
        if not items:
            return
        ids = [item.data(Qt.UserRole) for item in items]
        mime = QMimeData()
        if len(ids) == 1:
            mime.setData(MIME, ids[0].encode('utf-8'))
        else:
            mime.setData(GROUP_MIME, json.dumps(ids).encode('utf-8'))
        title = items[0].data(Qt.UserRole + 1) if len(items) == 1 else tr("Samples selected: {count}").format(count=len(items))
        _start_drag(self, mime, Qt.CopyAction, title)


class DeviceDropButton(QPushButton):
    sample_entered = Signal()
    sample_dropped = Signal(str)

    def __init__(self, text="", *, slot=None):
        super().__init__(text)
        self.slot = slot
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if not event.mimeData().hasFormat(MIME) or self.property("occupied"):
            event.ignore()
            return
        event.setDropAction(Qt.CopyAction)
        event.accept()
        if self.slot is not None:
            self.setDown(True)
            QToolTip.showText(self.mapToGlobal(event.position().toPoint()), f"→ {self.slot:03d}", self)
        self.sample_entered.emit()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(MIME) and not self.property("occupied"):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            self._reset_target()
            event.ignore()

    def _reset_target(self):
        if self.slot is not None:
            self.setDown(False)
            QToolTip.hideText()

    def dragLeaveEvent(self, event):
        self._reset_target()
        event.accept()

    def dropEvent(self, event):
        self._reset_target()
        if self.slot is None or self.property("occupied") or not event.mimeData().hasFormat(MIME):
            event.ignore()
            return
        try:
            sample_id = bytes(event.mimeData().data(MIME)).decode("utf-8")
        except UnicodeDecodeError:
            event.ignore()
            return
        if not sample_id:
            event.ignore()
            return
        event.setDropAction(Qt.CopyAction)
        event.accept()
        QTimer.singleShot(0, self, lambda: self.sample_dropped.emit(sample_id))


class SlotDropTable(QTableWidget):
    slot_moved = Signal(int, int)
    sample_dropped = Signal(int, str)
    samples_dropped = Signal(int, list)

    def __init__(self, *args):
        super().__init__(*args)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(False)
        self._drop_row = -1

    def startDrag(self, actions):
        row = self.currentRow()
        item = self.item(row, 1) if row >= 0 else None
        if item is None or item.data(Qt.UserRole) is None:
            return
        mime = QMimeData()
        mime.setData(SLOT_MIME, str(row).encode('ascii'))
        _start_drag(self, mime, Qt.MoveAction, f"{row:03d} · {item.text()}")
        self._set_drop_row(-1)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(SLOT_MIME):
            if event.source() is self:
                event.setDropAction(Qt.MoveAction)
                event.accept()
            else:
                event.ignore()
        elif event.mimeData().hasFormat(MIME) or event.mimeData().hasFormat(GROUP_MIME):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def _set_drop_row(self, row):
        if row != self._drop_row:
            self._drop_row = row
            self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_row < 0:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        light = self.palette().base().color().lightness() >= 128
        accent = QColor("#8b4856" if light else "#d3949b")
        fill = QColor(accent)
        fill.setAlpha(33)
        painter.setPen(QPen(accent, 1.5))
        painter.setBrush(fill)
        row = self._drop_row
        painter.drawRoundedRect(
            QRectF(1, self.rowViewportPosition(row) + 1, self.viewport().width() - 2, self.rowHeight(row) - 2),
            5, 5,
        )

        target = f"→ {row:03d}"
        width = self.fontMetrics().horizontalAdvance(target) + 14
        height = min(22, self.rowHeight(row) - 4)
        badge = QRect(self.viewport().width() - width - 10,
                      self.rowViewportPosition(row) + (self.rowHeight(row) - height) // 2,
                      width, height)
        painter.setBrush(QColor("#f7eaed" if light else "#3e2d31"))
        painter.drawRoundedRect(badge, 5, 5)
        painter.setPen(accent)
        painter.drawText(badge, Qt.AlignCenter, target)

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)
        row = self.rowAt(event.position().toPoint().y())
        if row < 0 or not event.isAccepted():
            self._set_drop_row(-1)
            event.ignore()
            return
        self._set_drop_row(row)

    def dragLeaveEvent(self, event):
        self._set_drop_row(-1)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._set_drop_row(-1)
        row = self.rowAt(event.position().toPoint().y())
        if event.mimeData().hasFormat(SLOT_MIME):
            try:
                source = int(bytes(event.mimeData().data(SLOT_MIME)).decode('ascii'))
            except (ValueError, UnicodeDecodeError):
                event.ignore()
                return
            if event.source() is not self or not 0 <= source < self.rowCount() or not 0 <= row < self.rowCount():
                event.ignore()
                return
            event.setDropAction(Qt.MoveAction)
            event.accept()
            self.slot_moved.emit(source, row)
            return
        if row < 0 or not (event.mimeData().hasFormat(MIME) or event.mimeData().hasFormat(GROUP_MIME)):
            event.ignore()
            return
        try:
            if event.mimeData().hasFormat(GROUP_MIME):
                ids = json.loads(bytes(event.mimeData().data(GROUP_MIME)).decode('utf-8'))
                if not isinstance(ids, list) or not 1 <= len(ids) <= 200 or any(not isinstance(i, str) or not i for i in ids):
                    raise ValueError('Invalid group')
            else:
                ids = [bytes(event.mimeData().data(MIME)).decode('utf-8')]
                if not ids[0]:
                    raise ValueError('Empty sample ID')
        except (UnicodeDecodeError, ValueError):
            event.ignore()
            return
        event.setDropAction(Qt.CopyAction)
        event.accept()
        if len(ids) == 1:
            self.sample_dropped.emit(row, ids[0])
        else:
            self.samples_dropped.emit(row, ids)
