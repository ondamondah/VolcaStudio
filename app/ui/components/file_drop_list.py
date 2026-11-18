from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QListWidget

from app.audio.converter import IMPORT_EXTENSIONS


class FileDropList(QListWidget):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDefaultDropAction(Qt.CopyAction)

    @staticmethod
    def audio_paths(mime):
        return list(dict.fromkeys(
            url.toLocalFile() for url in mime.urls()
            if url.isLocalFile()
            and Path(url.toLocalFile()).is_file()
            and Path(url.toLocalFile()).suffix.lower() in IMPORT_EXTENSIONS
        ))

    def dragEnterEvent(self, event):
        if self.audio_paths(event.mimeData()):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        paths = self.audio_paths(event.mimeData())
        if paths:
            event.setDropAction(Qt.CopyAction)
            event.accept()
            self.files_dropped.emit(paths)
        else:
            event.ignore()
