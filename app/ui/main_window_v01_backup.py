from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget
)

from app.core.logging_config import configure_logging
from app.device.midi import MidiService
from app.ui.theme import APP_STYLE

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        configure_logging()
        self.setWindowTitle("Volca Sample 2 Studio")
        self.setMinimumSize(760, 620)
        self.resize(900, 700)
        self.midi = MidiService(self._incoming_message)
        self.midi_message_received.connect(self._append_incoming)
        self._build_ui()
        self.refresh_ports()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._check_ports)
        self.timer.start(2000)

    def _build_ui(self):
        self.setStyleSheet(APP_STYLE)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("VOLCA SAMPLE 2 STUDIO")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()
        self.connection_badge = QLabel("●  Not connected")
        self.connection_badge.setStyleSheet("color: #e6b566; font-weight: 600;")
        header.addWidget(self.connection_badge)
        layout.addLayout(header)

        subtitle = QLabel("Device connection · MIDI diagnostics")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)
        panel_layout.setSpacing(12)

        section = QLabel("MIDI ports")
        section.setObjectName("section")
        panel_layout.addWidget(section)

        panel_layout.addWidget(QLabel("MIDI INPUT"))
        self.input_combo = QComboBox()
        panel_layout.addWidget(self.input_combo)

        panel_layout.addWidget(QLabel("MIDI OUTPUT"))
        self.output_combo = QComboBox()
        panel_layout.addWidget(self.output_combo)

        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh ports")
        self.refresh_button.clicked.connect(self.refresh_ports)
        buttons.addWidget(self.refresh_button)
        self.connect_button = QPushButton("Open selected ports")
        self.connect_button.clicked.connect(self.open_selected_ports)
        buttons.addWidget(self.connect_button)
        self.disconnect_button = QPushButton("Close ports")
        self.disconnect_button.clicked.connect(self.close_ports)
        self.disconnect_button.setEnabled(False)
        buttons.addWidget(self.disconnect_button)
        panel_layout.addLayout(buttons)
        layout.addWidget(panel)

        diagnostics = QLabel("Diagnostics")
        diagnostics.setObjectName("section")
        layout.addWidget(diagnostics)

        status_row = QHBoxLayout()
        self.input_status = QLabel("Input: waiting")
        self.input_status.setObjectName("muted")
        self.output_status = QLabel("Output: waiting")
        self.output_status.setObjectName("muted")
        status_row.addWidget(self.input_status)
        status_row.addStretch()
        status_row.addWidget(self.output_status)
        layout.addLayout(status_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("MIDI diagnostic events will appear here.")
        layout.addWidget(self.log_view, 1)

        footer = QLabel("V0.1.0 · Safe mode — no MIDI or SysEx data is transmitted")
        footer.setObjectName("muted")
        footer.setAlignment(Qt.AlignCenter)
        layout.addWidget(footer)

    def _log(self, message: str):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {message}")
        logger.info(message)

    def refresh_ports(self):
        current_in = self.input_combo.currentText()
        current_out = self.output_combo.currentText()
        try:
            inputs = self.midi.input_names()
            outputs = self.midi.output_names()
        except Exception as exc:
            self._log(f"Could not enumerate MIDI ports: {exc}")
            QMessageBox.warning(self, "MIDI error", str(exc))
            return

        self.input_combo.clear()
        self.output_combo.clear()
        self.input_combo.addItems(inputs)
        self.output_combo.addItems(outputs)

        if current_in in inputs:
            self.input_combo.setCurrentText(current_in)
        if current_out in outputs:
            self.output_combo.setCurrentText(current_out)

        self._log(f"Port scan: {len(inputs)} input(s), {len(outputs)} output(s)")
        self._update_status()

    def _check_ports(self):
        try:
            inputs = self.midi.input_names()
            outputs = self.midi.output_names()
            selected_in = self.input_combo.currentText()
            selected_out = self.output_combo.currentText()

            if self.midi.input_open and selected_in not in inputs:
                self.midi.close_input()
                self._log("Selected MIDI input disappeared; closed it.")
            if self.midi.output_open and selected_out not in outputs:
                self.midi.close_output()
                self._log("Selected MIDI output disappeared; closed it.")

            known_in = {self.input_combo.itemText(i) for i in range(self.input_combo.count())}
            known_out = {self.output_combo.itemText(i) for i in range(self.output_combo.count())}
            if set(inputs) != known_in or set(outputs) != known_out:
                self.refresh_ports()
            else:
                self._update_status()
        except Exception as exc:
            self._log(f"Port monitoring error: {exc}")

    def open_selected_ports(self):
        try:
            input_name = self.input_combo.currentText()
            output_name = self.output_combo.currentText()
            if not input_name and not output_name:
                QMessageBox.information(self, "Select a port", "No MIDI ports are available. Connect the Volca by USB and refresh.")
                return
            if input_name:
                self.midi.open_input(input_name)
                self._log(f"Opened MIDI input: {input_name}")
            if output_name:
                self.midi.open_output(output_name)
                self._log(f"Opened MIDI output: {output_name} (no data sent)")
            self._update_status()
        except Exception as exc:
            self._log(f"Could not open MIDI port: {exc}")
            QMessageBox.warning(self, "MIDI connection error", str(exc))
            self._update_status()

    def close_ports(self):
        self.midi.close()
        self._log("MIDI ports closed.")
        self._update_status()

    def _incoming_message(self, message: str):
        # Mido callback may run on a backend thread; queue UI update through a Qt signal
        self.midi_message_received.emit(message)

    from PySide6.QtCore import Signal
    midi_message_received = Signal(str)

    def _append_incoming(self, message: str):
        self._log(f"IN  {message}")

    def _update_status(self):
        in_open = self.midi.input_open
        out_open = self.midi.output_open
        self.input_status.setText("Input: open" if in_open else "Input: waiting")
        self.output_status.setText("Output: open" if out_open else "Output: waiting")
        connected = in_open or out_open
        self.connection_badge.setText("●  MIDI ports open" if connected else "●  Not connected")
        self.connection_badge.setStyleSheet(
            "color: #8fcea5; font-weight: 600;" if connected
            else "color: #e6b566; font-weight: 600;"
        )
        self.disconnect_button.setEnabled(connected)

    def closeEvent(self, event):
        self.timer.stop()
        self.midi.close()
        event.accept()
