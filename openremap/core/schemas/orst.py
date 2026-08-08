"""Pydantic models for the .orst (OpenRemap Saved Tune) format 2.0.

The .orst format is the workspace-native tune file. It carries the same
instruction shape as a .remap recipe but with minimal metadata — just
enough for the editor to reopen the tune, display its history, and export
it as a portable recipe.

Consumed by openremap-studio. Not exposed through the CLI or TUI.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────

class InstructionStatus(str, Enum):
    """Validation status of an instruction within a tune."""

    NORMAL = "Normal"
    UNRESOLVED = "Unresolved"


# ── Instruction ────────────────────────────────────────────────────────────

class TuneInstructionFlag(BaseModel):
    """Annotation flag attached to a tune instruction."""

    model_config = {"extra": "ignore"}

    kind: str = ""          # e.g. "VIN_SUSPECT", "CHECKSUM_SUSPECT", "LOW_ENTROPY"
    reason: str = ""        # human-readable explanation
    confidence: float = 0.0 # 0.0–1.0
    action: str = ""        # "WARN" | "SKIP" | "REVIEW"


class TuneInstruction(BaseModel):
    """A single changed byte range within a tune — same shape as a recipe
    instruction, plus a ``status`` field for binary-rebase tracking."""

    model_config = {"extra": "ignore"}

    offset: int = 0
    offset_hex: str = ""
    size: int = 0
    ob: str = ""            # original bytes — uppercase hex
    mb: str = ""            # modified bytes — uppercase hex
    ctx: str = ""           # context before — uppercase hex
    context_after: str = "" # context after — uppercase hex
    context_size: int = 0
    ctx_entropy: Optional[float] = None
    ctx_unique: Optional[bool] = None
    ctx_expanded: Optional[bool] = None
    description: str = ""
    flags: list[TuneInstructionFlag] = Field(default_factory=list)
    status: InstructionStatus = InstructionStatus.NORMAL


# ── Source binary reference ───────────────────────────────────────────────

class SourceBinaryRef(BaseModel):
    """Identity of the binary the tune was cooked against."""

    sha256: str = ""
    file_size: int = 0
    path_hint: str = ""     # last-known filename, not authoritative


# ── Tune file (top-level) ─────────────────────────────────────────────────

class OrstFile(BaseModel):
    """Top-level .orst file — the saved tune."""

    model_config = {"extra": "ignore"}

    orst: str = "2.0"                               # schema version
    id: str = ""                                    # "orst_<32hex>" — stable across renames
    name: str = ""                                  # display name, user-editable
    message: Optional[str] = None                    # commit message from Ctrl+S
    source_binary: SourceBinaryRef = Field(default_factory=SourceBinaryRef)
    base_tune_id: Optional[str] = None               # forked from this tune, null = from stock
    created_at: str = ""                             # ISO 8601 UTC
    modified_at: str = ""                            # ISO 8601 UTC
    archived_at: Optional[str] = None                # only set for archive snapshots
    instructions: list[TuneInstruction] = Field(default_factory=list)
