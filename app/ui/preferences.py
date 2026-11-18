from PySide6.QtCore import QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.ui.translations import translated


def preferences():
    return QSettings("VolcaStudio", "VolcaStudio")


def startup_options():
    settings = preferences()
    return tuple(settings.value(key, default, type=str) for key, default in (
        ("language", "en"), ("theme", "dark"), ("density", "standard"),
    ))


class SettingsPage(QWidget):
    def __init__(self, library_root, select_language, select_density):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        title = translated(QLabel(), "Settings")
        title.setObjectName("title")
        layout.addWidget(title)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        settings = preferences()
        for key, label, options in (
            ("language", "Language", (("en", "English"), ("ru", "Русский"))),
            ("density", "Layout", (("standard", "Standard"), ("compact", "Compact"))),
        ):
            combo = QComboBox()
            combo.setObjectName(key)
            combo.setProperty("settingsChoice", True)
            for value, caption in options:
                combo.addItem(caption, value)
            translated(combo, tuple(caption for _, caption in options), "items")
            index = combo.findData(settings.value(key, options[0][0]))
            combo.setCurrentIndex(max(0, index))
            if key == "language":
                combo.currentIndexChanged.connect(lambda _, control=combo: select_language(control.currentData()))
            else:
                combo.currentIndexChanged.connect(lambda _, control=combo: select_density(control.currentData()))
            form.addRow(translated(QLabel(), label), combo)
        layout.addLayout(form)

        folders = QFormLayout()
        for label, path in (("Library", library_root), ("Device backups", library_root / "Device Recovery"),
                            ("Pattern backups", library_root / "Pattern Recovery")):
            button = translated(QPushButton(), "Open folder")
            button.setToolTip(str(path))
            button.clicked.connect(lambda _, target=path: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(target if target.is_dir() else library_root))
            ))
            folders.addRow(translated(QLabel(), label), button)
        layout.addLayout(folders)
        layout.addStretch()
