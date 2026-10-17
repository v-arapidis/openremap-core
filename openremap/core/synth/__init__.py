"""
openremap.core.synth — deterministic synthetic ECU dumps.

Builds ECU-*like* binaries **from scratch**: flash-layout sectors, calibration
axes, smooth map surfaces, optionally a checksum.  No byte of any real ROM is
involved, so the output carries no OEM content and can be committed, shipped as
a demo fixture, or attached to a bug report — which is the whole point (see
`agents/behavior/synth.md`).

Everything is deterministic: the same parameters + seed produce byte-identical
output everywhere, so a test corpus can store the **parameters** instead of the
file, and an intentional engine change shows up as a reviewable diff in the
expected outputs.

What it does *not* do: produce files that ``identify`` recognises as a real
family.  Generated dumps have no manufacturer fingerprints, so ``identify``
correctly reports ``Unknown`` — that negative result is itself worth pinning.
"""

from __future__ import annotations

from .generator import (
    GENERATOR_VERSION,
    GeneratedDump,
    PlantedMap,
    Sector,
    generate_dump,
)

__all__ = [
    "GENERATOR_VERSION",
    "GeneratedDump",
    "PlantedMap",
    "Sector",
    "generate_dump",
]
