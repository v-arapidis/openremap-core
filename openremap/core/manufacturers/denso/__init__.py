"""
Denso extractor registry.

Exposes an ordered EXTRACTORS list consumed by the top-level manufacturer
registry.  Intra-brand priority order:

  1. 68HC16Y5 — 160/192 KB 16-bit Motorola HC16 units (2001-2004).  Unique
                small size.
  2. SH7055   — 512 KB SuperH units (2004-2007).  Unique size.
  3. Diesel   — 1 MB EE20 diesel unit (2009-2012).  Shares the 1 MB size
                with SH7058, but its identity block (0x4000, "Cpyr.DENSO",
                K###ZQ2DT tag) is disjoint from every petrol layout, so it
                runs first for defence-in-depth.
  4. SH7058   — 1 MB SuperH CAN units (2006-2014), A1/A2 layouts plus
                shifted dumps.

All four families are Denso hardware running Subaru firmware; the CAL ID
conventions they share live in ``denso.base``.  Family labels were
oracle-corrected 2026-09-02 (one CPU generation off) — see the Denso
Subaru relabel entry in ``changelog/0.7.6.md``.
"""

from openremap.core.manufacturers.base import BaseManufacturerExtractor
from openremap.core.manufacturers.denso.diesel import DensoDieselExtractor
from openremap.core.manufacturers.denso.hc16y5 import DensoHC16Y5Extractor
from openremap.core.manufacturers.denso.sh7055 import DensoSH7055Extractor
from openremap.core.manufacturers.denso.sh7058 import DensoSH7058Extractor

# ---------------------------------------------------------------------------
# Registry — intra-brand priority order (first match wins).
# ---------------------------------------------------------------------------

EXTRACTORS: list[BaseManufacturerExtractor] = [
    DensoHC16Y5Extractor(),
    DensoSH7055Extractor(),
    DensoDieselExtractor(),
    DensoSH7058Extractor(),
]
