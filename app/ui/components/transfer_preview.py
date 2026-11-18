from app.ui.translations import tr
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

from app.ui.components.overlay_scrollbar import OverlayScrollBar
from app.ui.preferences import startup_options


class TransferPreviewTable(QTableWidget):
    def __init__(self, rows):
        super().__init__(len(rows), 3)
        self.setHorizontalHeaderLabels([tr("Slot"), tr("Sample"), tr("Status")])
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setShowGrid(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(28 if startup_options()[2] == "compact" else 30)
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.setColumnWidth(0, 84)
        self.overlay_scroll = OverlayScrollBar(self)

        for row, (number, name, status) in enumerate(rows):
            for column, text in enumerate(("—" if number is None else f"{number:03d}", name, status)):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
                if column == 0:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                else:
                    item.setToolTip(text)
                self.setItem(row, column, item)
