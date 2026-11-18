"""An in-place vertical thumb for the Samples lists."""

from PySide6.QtCore import QEvent, QSignalBlocker, QTimer, Qt
from PySide6.QtWidgets import QScrollBar


class OverlayScrollBar(QScrollBar):
    def __init__(self, area):
        super().__init__(Qt.Vertical, area)
        self.area = area
        self.viewport = area.viewport()
        self.source = area.verticalScrollBar()
        self.hovered = False
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.hide()

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(650)
        self.hide_timer.timeout.connect(self._hide_when_idle)
        self.layout_timer = QTimer(self)
        self.layout_timer.setSingleShot(True)
        self.layout_timer.timeout.connect(self._sync_geometry)

        self.source.rangeChanged.connect(self._sync_geometry)
        self.source.valueChanged.connect(self._scrolled)
        self.valueChanged.connect(self.source.setValue)
        self.sliderReleased.connect(self.hide_timer.start)
        area.installEventFilter(self)
        self.viewport.installEventFilter(self)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._sync_geometry()

    def eventFilter(self, watched, event):
        if watched is self.area or watched is self.viewport:
            if event.type() in (QEvent.Resize, QEvent.Move, QEvent.Show):
                self.layout_timer.start(0)
            if watched is self.area:
                if event.type() == QEvent.Enter:
                    self.hovered = True
                    self._show_thumb()
                elif event.type() == QEvent.Leave:
                    self.hovered = False
                    self.hide_timer.start()
        return super().eventFilter(watched, event)

    def _sync_geometry(self, *_):
        viewport = self.viewport.geometry()
        width = self.sizeHint().width()
        self.setGeometry(viewport.right() - width, viewport.top(), width, viewport.height())
        with QSignalBlocker(self):
            self.setRange(self.source.minimum(), self.source.maximum())
            self.setPageStep(self.source.pageStep())
            self.setSingleStep(self.source.singleStep())
            self.setValue(self.source.value())
        if self.minimum() == self.maximum():
            self.hide()
        elif self.hovered:
            self._show_thumb()

    def _scrolled(self, value):
        self.setValue(value)
        self._show_thumb()
        self.hide_timer.start()

    def _show_thumb(self):
        if self.maximum() > self.minimum() and self.area.isVisible():
            self.show()
            self.raise_()

    def _hide_when_idle(self):
        if not self.hovered and not self.isSliderDown():
            self.hide()
