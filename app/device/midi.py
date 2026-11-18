from __future__ import annotations

import mido
from typing import Callable


class MidiService:
    """Small, safe MIDI layer for V0.1. No outgoing messages are sent."""

    def __init__(self, on_message: Callable[[str], None] | None = None):
        self._input = None
        self._output = None
        self._on_message = on_message or (lambda message: None)

    @staticmethod
    def input_names() -> list[str]:
        return mido.get_input_names()

    @staticmethod
    def output_names() -> list[str]:
        return mido.get_output_names()

    def open_input(self, name: str) -> None:
        self.close_input()
        self._input = mido.open_input(
            name,
            callback=lambda message: self._on_message(str(message))
        )

    def open_output(self, name: str) -> None:
        self.close_output()
        # Open only. V0.1 intentionally sends no MIDI data.
        self._output = mido.open_output(name)

    def close_input(self) -> None:
        if self._input is not None:
            self._input.close()
            self._input = None

    def close_output(self) -> None:
        if self._output is not None:
            self._output.close()
            self._output = None

    def close(self) -> None:
        self.close_input()
        self.close_output()

    @property
    def input_open(self) -> bool:
        return self._input is not None

    @property
    def output_open(self) -> bool:
        return self._output is not None
