"""
Hitachi extractor registry.

Exposes an ordered EXTRACTORS list consumed by the top-level manufacturer
registry.  Currently a single family:

  1. SH72546 — 1.5/2 MB units (2013+ Subaru applications).

The SH72546 CAL ID format mirrors the Denso Subaru convention, but the
binaries are Hitachi hardware (Hitachi 33920-xxxxx part numbers) with no
Denso copyright string, hence a separate manufacturer package.
"""

from openremap.core.manufacturers.base import BaseManufacturerExtractor
from openremap.core.manufacturers.hitachi.sh72546 import HitachiSH72546Extractor

# ---------------------------------------------------------------------------
# Registry — intra-brand priority order (first match wins).
# ---------------------------------------------------------------------------

EXTRACTORS: list[BaseManufacturerExtractor] = [
    HitachiSH72546Extractor(),
]
