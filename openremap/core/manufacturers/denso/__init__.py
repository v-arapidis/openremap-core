"""
Denso extractor registry.

Exposes an ordered EXTRACTORS list consumed by the top-level manufacturer
registry.  Intra-brand priority order:

  1. SH7055  — 160/192 KB 16-bit units (2001-2004).  Unique small size.
  2. SH7058  — 512 KB 32-bit units (2004-2007).  Unique size.
  3. Diesel  — 1 MB EE20 diesel unit (2009-2012).  Shares the 1 MB size
               with SH72531, but its identity block (0x4000, "Cpyr.DENSO",
               K###ZQ2DT tag) is disjoint from every petrol layout, so it
               runs first for defence-in-depth.
  4. SH72531 — 1 MB 32-bit CAN units (2006-2014), A1/A2 layouts plus
               shifted dumps.

All four families are Denso hardware running Subaru firmware; the CAL ID
conventions they share live in ``denso.base``.
"""

from openremap.core.manufacturers.base import BaseManufacturerExtractor
from openremap.core.manufacturers.denso.diesel import DensoDieselExtractor
from openremap.core.manufacturers.denso.sh7055 import DensoSH7055Extractor
from openremap.core.manufacturers.denso.sh7058 import DensoSH7058Extractor
from openremap.core.manufacturers.denso.sh72531 import DensoSH72531Extractor

# ---------------------------------------------------------------------------
# Registry — intra-brand priority order (first match wins).
# ---------------------------------------------------------------------------

EXTRACTORS: list[BaseManufacturerExtractor] = [
    DensoSH7055Extractor(),
    DensoSH7058Extractor(),
    DensoDieselExtractor(),
    DensoSH72531Extractor(),
]
