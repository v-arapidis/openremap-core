"""Phase 2 sweep: signal strength across the C166 family list.

For each binary: identity (family/arch), code bytes, direct-mem ref count,
best T3 window hits at >= 0.85, and the high-score table offset range
(explains which window is best).
"""
from __future__ import annotations

import sys

from openremap.core.services.checksums.nefmoto import _parse_instruction, _parse_mov, _parse_movb
from openremap.core.services.identify.identifier import identify_ecu
from openremap.core.services.maps.layout import segment
from openremap.core.services.maps.map_hunter import scan_map_tables

_DIRECT_MEM = (0x84, 0x94, 0xA4, 0xB4, 0xF2, 0xF3, 0xF6, 0xF7)


def sweep(path: str) -> None:
    data = open(path, "rb").read()
    n = len(data)
    ident = identify_ecu(data, path.split("/")[-1])
    tables = scan_map_tables(data, min_score=0.55, max_series_tables=16)
    regions = segment(data, tables=tables)
    codes = [(r.start, r.end) for r in regions if r.kind == "code"]

    mem_offs: list[int] = []
    for s, e in codes:
        off = s
        while off + 1 < e:
            name, size = _parse_instruction(data, off)
            b0 = data[off]
            if name == "MOV":
                r = _parse_mov(data, off)
                if r is not None and r[1] >= 2 and b0 in _DIRECT_MEM:
                    mem_offs.append(r[0])
            elif name == "MOVB":
                r = _parse_movb(data, off)
                if r is not None and r[1] >= 2 and b0 in _DIRECT_MEM:
                    mem_offs.append(r[0])
            off += size

    hi = [t for t in tables if t.score >= 0.85]
    hi_spans = []
    for t in hi:
        row_bytes = t.cols * t.cell_width
        if t.stride is not None and t.stride != row_bytes:
            for r in range(t.rows):
                hi_spans.append((t.offset + r * t.stride, t.offset + r * t.stride + row_bytes))
        else:
            hi_spans.append((t.offset, t.offset + t.rows * row_bytes))

    def hits(fs):
        return sum(1 for f in fs if any(s <= f < e for s, e in hi_spans))

    best = max(((hits([(o & 0x3FFF) + w for o in mem_offs]), w) for w in range(0, n, 0x4000)), default=(0, 0))
    lo = min((t.offset for t in hi), default=0)
    hi_off = max((t.offset + t.rows * (t.cols * t.cell_width) for t in hi), default=0)
    print(
        f"{ident.get('ecu_family', '?') or '?':<12} {path.split('/')[-1][:38]:<40} "
        f"n={n:<8} code={sum(e - s for s, e in codes):<8} refs={len(mem_offs):<7} "
        f"T3hi85={best[0]:<6} W=0x{best[1]:X}  hi85_tables={len(hi)}@{hex(lo)}-{hex(hi_off)}"
    )


if __name__ == "__main__":
    for p in sys.argv[1:]:
        sweep(p)
