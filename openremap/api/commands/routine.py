"""
openremap.api.commands.routine — routine: read a code routine as pseudo-code.

Thin gate: path I/O here; the bytes→dict logic lives in
``openremap.core.arch.routine.routine_binary``.  The offset string parsing
(``int(text, 0)``) stays in the CLI's Typer layer; the core takes an ``int``.
"""

from __future__ import annotations

from pathlib import Path

from openremap.api._io import load_binary
from openremap.api.errors import GuardError
from openremap.api.registry import register
from openremap.core.arch.routine import routine_binary

# Re-export for the CLI.
__all__ = ["routine_binary", "routine"]


@register(
    "routine",
    kind="command",
    description="Read a code routine as readable pseudo-code.",
    input_schema={
        "path": {"type": "str", "required": True, "description": "Path to the binary."},
        "offset": {"type": "int", "required": True, "description": "File offset to disassemble at."},
        "arch": {"type": "str", "description": "Decoder override (c166|tricore|sh|x86|m680x|m68k|ppc)."},
        "before": {"type": "int", "default": 8, "min": 0},
        "after": {"type": "int", "default": 60, "min": 0},
    },
)
def routine(params: dict) -> dict:
    path = params["path"]
    try:
        return routine_binary(
            load_binary(path),
            Path(path).name,
            int(params["offset"]),
            arch=params.get("arch"),
            before=int(params.get("before", 8)),
            after=int(params.get("after", 60)),
        )
    except ValueError as exc:
        raise GuardError(str(exc)) from exc
