"""
A task card — the unit the Home screen is made of.

It is focusable (Tab / arrows), clickable, and activated with Enter or by its
number key (handled by the screen, which owns the ordering). Everything the card
shows is a promise about what pressing it does, so the three lines are fixed:
name, the inputs it needs, and what it produces.
"""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static

__all__ = ["TaskCard"]


class TaskCard(Static):
    """One task on the Home screen."""

    can_focus: ClassVar[bool] = True

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "activate", "Open", show=False),
    ]

    class Activated(Message):
        """Posted when the card is chosen (Enter, click, or its number key)."""

        def __init__(self, key: str) -> None:
            super().__init__()
            self.key = key

    def __init__(
        self,
        key: str,
        number: int,
        title: str,
        needs: str,
        produces: str,
        *,
        enabled: bool = True,
    ) -> None:
        super().__init__(classes="card")
        self.key = key
        self.number = number
        self.title = title
        self.needs = needs
        self.produces = produces
        self.enabled = enabled

    def render(self) -> Text:
        text = Text()
        text.append(f" {self.number}  ", style="bold #4ad6a8" if self.enabled else "#3a4557")
        text.append(self.title, style="bold #e8eef7" if self.enabled else "#5a6b85")
        if not self.enabled:
            text.append("  · soon", style="#3a4557")
        text.append("\n")
        text.append(f"    {self.needs}", style="#8aa0c0")
        text.append("\n")
        text.append(f"    → {self.produces}", style="#5a6b85")
        return text

    def action_activate(self) -> None:
        if self.enabled:
            self.post_message(self.Activated(self.key))

    def on_click(self) -> None:
        self.focus()
        self.action_activate()

    def watch_has_focus(self) -> None:  # pragma: no cover - styling hook
        self.set_class(self.has_focus, "focused")
