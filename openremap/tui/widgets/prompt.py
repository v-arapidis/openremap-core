"""
A one-line prompt — the smallest way to ask the operator for a word.

The TUI has no text fields except the file picker's path box, and a couple of
flows genuinely need a word typed in (a brand, a family name). This is that:
one question, one line, Enter to accept, Esc to cancel, and an optional default
shown as the placeholder.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

__all__ = ["PromptScreen"]


class PromptScreen(ModalScreen[str | None]):
    """Ask for one line of text. Returns it, or ``None`` on cancel."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def __init__(self, question: str, *, hint: str = "", value: str = "") -> None:
        super().__init__()
        self.question = question
        self.hint = hint
        self.value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-panel"):
            yield Static(self.question, id="prompt-question")
            if self.hint:
                yield Static(self.hint, id="prompt-hint")
            yield Input(value=self.value, placeholder="type and press Enter", id="prompt-input")
            yield Static("Enter accept   Esc cancel", id="prompt-keys")

    def on_mount(self) -> None:
        field = self.query_one("#prompt-input", Input)
        field.focus()
        field.cursor_position = len(field.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.dismiss(text or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
