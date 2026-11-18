SELECTION_BACKGROUND = "#503239"
ROW_SELECTION_BACKGROUND = "#3e2d31"

APP_STYLE = """
QWidget {
    background-color: #212121;
    color: #eee9ea;
    font-size: 13px;
}
QLabel { background: transparent; }
QLabel#muted { color: #afa5a8; }
QLabel[warningText="true"] { color: #e6b566; }
QLabel#transferMemory {
    background: #292627;
    border: 1px solid #3c3436;
    border-radius: 8px;
    padding: 7px 10px;
}
QLabel#title { font-size: 23px; font-weight: 650; }
QLabel#section { font-size: 14px; font-weight: 600; }
QLabel#devicePanelTitle { font-size: 19px; font-weight: 650; }
QWidget#deviceControls { background: transparent; }
QWidget#deviceControls QPushButton, QPushButton#deviceMapToggle { min-height: 20px; padding: 5px 9px; }
QWidget#deviceControls QPushButton[menuButton="true"] { padding-right: 24px; }
QWidget#deviceControls QPushButton[menuButton="true"]::menu-indicator { right: 8px; }
QPushButton#deviceMapToggle:checked { background: @selection@; border-color: #65464d; }
QTableWidget#deviceSampleTable { selection-background-color: @row_selection@; }

QFrame#panel {
    background: #181818;
    border: 1px solid #303030;
    border-radius: 14px;
}
QPushButton {
    background: #303030;
    color: #eee9ea;
    border: 1px solid #41383b;
    border-radius: 9px;
    padding: 8px 12px;
}
QPushButton:hover { background: #3a3a3a; }
QPushButton:pressed { background: #454545; }
QPushButton:disabled { background: #262626; color: #888888; border-color: #333333; }
QPushButton:focus { border-color: #929b94; }
QPushButton#patternPreview, QPushButton#themeButton { padding: 0; }
QWidget#patternRow, QWidget#slotControls { background: transparent; }
QPushButton#samplePlay {
    background: transparent;
    border: 1px solid #454042;
    border-radius: 6px;
    padding: 0;
}
QPushButton#samplePlay:hover { background: #3e2d31; border-color: #76545a; }
QPushButton#samplePlay:pressed { background: #503239; }
QSpinBox#patternTempo {
    background: #303030;
    border: 1px solid #41383b;
    border-radius: 9px;
    padding: 0 12px;
}
QSpinBox#patternTempo:focus { border-color: #929b94; }
QSpinBox#patternTempo:disabled { background: #262626; color: #888888; border-color: #333333; }
QSpinBox#patternTempo QLineEdit { background: transparent; border: 0; padding: 0; min-height: 0; }
QFrame#patternSoundPanel { background: #191919; border: 1px solid #363033; border-radius: 9px; }
QPushButton#patternPart { padding: 0; background: transparent; border-color: transparent; }
QPushButton#patternPart:hover { background: #292929; }
QPushButton#patternPart:checked { background: @selection@; border-color: #65464d; }
QPushButton#patternPart:focus { border-color: #929b94; }
QComboBox#patternSamplePicker { padding: 3px 10px; }
QPushButton#activeStep { background: #292929; border: 1px solid #414141; border-radius: 5px; padding: 0; }
QPushButton#activeStep:checked { background: #a6686c; border-color: #bd7b80; }
QPushButton#activeStep:hover:enabled, QPushButton#activeStep:focus { border-color: #f0b2b6; }
QPushButton#patternStep { background: #292929; border: 1px solid #414141; border-radius: 5px; padding: 0; }
QPushButton#patternStep:checked { background: #a6686c; border-color: #bd7b80; }
QPushButton#patternStep[skipped="true"] { background: #212121; border-color: #353132; }
QPushButton#patternStep[skipped="true"]:checked { background: #503239; border-color: #65464d; }
QPushButton#patternStep:hover:enabled, QPushButton#patternStep:focus { border-color: #f0b2b6; }
QPushButton#patternStep[selectedStep="true"] { border: 2px solid #c68489; }
QPushButton#patternStep[selectedStep="true"]:checked { border-color: #f0b2b6; }
QPushButton#patternStep[selectedStep="true"][skipped="true"]:checked { border-color: #c68489; }
QPushButton[menuButton="true"] { padding-right: 30px; }
QPushButton[menuButton="true"]::menu-indicator {
    subcontrol-origin: border;
    subcontrol-position: center right;
    right: 11px;
    width: 9px;
    height: 9px;
}
QPushButton#primaryAction, QPushButton:default {
    background: #ecdde0;
    color: #201719;
    font-weight: 600;
}
QPushButton#primaryAction:hover, QPushButton:default:hover { background: #ffffff; }
QPushButton#primaryAction:disabled { background: #303030; color: #888888; }
QPushButton#navigation {
    background: transparent;
    border-color: transparent;
    text-align: left;
    padding: 9px 12px;
}
QPushButton#navigation:hover { background: #272727; }
QPushButton#navigation:checked {
    background: @selection@;
    border-color: #65464d;
    font-weight: 600;
}
QPushButton#navigation:focus { border-color: #929b94; }
QPushButton#navigation:disabled { color: #888888; }

QComboBox, QLineEdit {
    background: #292929;
    border: 1px solid #443a3d;
    border-radius: 9px;
    padding: 7px 10px;
    min-height: 20px;
    selection-background-color: @selection@;
    selection-color: #f6f8f7;
}
QComboBox:focus, QLineEdit:focus { border-color: #929b94; }
QComboBox[settingsChoice="true"] { combobox-popup: 0; }
QComboBox[settingsChoice="true"] QAbstractItemView { padding: 3px; }
QComboBox::drop-down { width: 24px; border: none; }
QListWidget, QTableWidget, QPlainTextEdit, QComboBox QAbstractItemView {
    background: #191919;
    border: 1px solid #363033;
    border-radius: 10px;
    selection-background-color: @selection@;
    selection-color: #f6f8f7;
    outline: 0;
}
QListWidget { show-decoration-selected: 1; }
QListWidget::item { padding: 5px 8px; border-radius: 5px; }
QListWidget#patternList::item, QListWidget#sampleLibrary::item { padding: 0; }
QListWidget::item:selected { background: @selection@; color: #f6f8f7; }
QListWidget#sampleLibrary::item:selected, QListWidget#patternList::item:selected,
QTableWidget#sampleSlotTable::item:selected {
    background: @row_selection@; color: #f6f8f7;
}
QListWidget::item:hover:!selected { background: #292929; }
QTableWidget { gridline-color: #303030; }
QHeaderView::section {
    background: #292929;
    color: #bdbdbd;
    padding: 7px;
    border: 0;
    border-bottom: 1px solid #414141;
}
QPlainTextEdit { padding: 8px; font-family: Menlo, monospace; font-size: 11px; }
QMenu {
    background: #292929;
    border: 1px solid #454545;
    border-radius: 9px;
    padding: 5px;
}
QMenu::item { padding: 7px 24px 7px 12px; border-radius: 5px; }
QMenu::item:selected:enabled { background: @selection@; }
QMenu::item:disabled { color: #909090; }
QMenu::separator { background: #454545; height: 1px; margin: 4px 8px; }
QSplitter::handle { background: #303030; }
QSplitter#samplePanes::handle:horizontal {
    background: transparent;
    border-left: 1px solid #323232;
    margin: 4px;
}
QSplitter#samplePanes::handle:horizontal:hover { border-left-color: #666666; }
QToolTip { background: #303030; color: #eee9ea; border: 1px solid #484848; padding: 5px; }
QPushButton#editorMode {
    background: #262626; border-radius: 6px; padding: 4px 10px;
    border: 1px solid #363033;
}
QPushButton#editorMode:checked { background: #503239; border-color: #65464d; }
QPushButton#slicePad {
    background: #262626; border-color: #363033; padding: 3px 6px;
    border-radius: 7px; font-weight: 600;
}
QLabel#slicePadKey { color: #afa5a8; background: transparent; font-size: 9px; }
QPushButton#slicePad:checked {
    background: #503239; border-color: #b77a82;
}
QPushButton#slicePad[excluded="true"] { color: #afa5a8; }
QPushButton#slicePad:disabled { color: #888888; background: #212121; }
QPushButton#deviceSlotCell {
    background: #262626; color: #afa5a8; border: 1px solid #363033;
    border-radius: 4px; padding: 0; font-size: 9px; min-height: 14px;
}
QPushButton#deviceSlotCell[occupied="true"] {
    background: #503239; color: #eee9ea; border-color: #65464d;
}
QPushButton#deviceSlotCell:hover { border-color: #929b94; }
QPushButton#deviceSlotCell:checked { border-color: #929b94; }
QPushButton#deviceSlotCell:pressed { background: #503239; border-color: #b77a82; }
QPushButton#deviceSlotCell:disabled { color: #888888; }
QProgressBar#deviceCapacity { background: #303030; border: none; border-radius: 4px; }
QProgressBar#deviceCapacity::chunk { background: #b77a82; border-radius: 4px; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0 4px; }
QScrollBar::handle {
    background: #5f5f5f;
    border: none;
    border-radius: 3px;
}
QScrollBar::handle:vertical { min-height: 28px; margin: 0 2px; }
QScrollBar::handle:horizontal { min-width: 28px; margin: 2px 0; }
QScrollBar::handle:hover { background-color: #757575; }
QScrollBar::handle:pressed { background-color: #909090; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
""".replace("@selection@", SELECTION_BACKGROUND).replace("@row_selection@", ROW_SELECTION_BACKGROUND)


def set_warning(widget, warning):
    if widget.property("warningText") == warning:
        return
    widget.setProperty("warningText", warning)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def is_light(theme):
    if theme == "system":
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        return QApplication.styleHints().colorScheme() == Qt.ColorScheme.Light
    return theme == "light"


def interface_style(theme, density):
    style = APP_STYLE
    if is_light(theme):
        import re

        light_colors = {
            "#212121": "#f6f2f2", "#eee9ea": "#292327", "#afa5a8": "#62565c",
            "#181818": "#efe9eb", "#191919": "#fffcfd", "#303030": "#ebe3e6",
            "#292929": "#f9f5f6", "#41383b": "#cbbdc3", "#443a3d": "#cbbdc3",
            "#363033": "#d4c7cd", "#414141": "#cabcc1", "#bdbdbd": "#51454a",
            "#888888": "#71646a", "#909090": "#71646a", "#262626": "#eae2e5",
            "#333333": "#d8ccd1", "#3a3a3a": "#e1d3d9", "#454545": "#d3c2c9",
            "#272727": "#e9dee3", "#65464d": "#c699a6", "#503239": "#ebcdd5",
            "#f6f8f7": "#35232b", "#ecdde0": "#70444f", "#201719": "#ffffff",
            "#ffffff": "#81515d", "#292627": "#f3e5e9", "#3c3436": "#d9bec7",
            "#3e2d31": "#ecd5dc", "#454042": "#cabbc2", "#76545a": "#a77482",
            "#e6b566": "#865721", "#a6686c": "#d8a0aa", "#bd7b80": "#8b4856",
            "#c68489": "#8b4856", "#b77a82": "#9f5965", "#f0b2b6": "#733440",
            "#929b94": "#8c5868", "#323232": "#d4c7cd", "#484848": "#c7b8be",
        }
        style = re.sub(r"#[0-9a-f]{6}\b", lambda match: light_colors.get(match[0], match[0]), style)
    if density == "compact":
        style += """
QPushButton { padding: 5px 9px; }
QPushButton#navigation { padding: 6px 10px; }
QComboBox, QLineEdit { padding-top: 4px; padding-bottom: 4px; }
QHeaderView::section { padding-top: 4px; padding-bottom: 4px; }
QMenu::item { padding-top: 4px; padding-bottom: 4px; }
"""
    return style
