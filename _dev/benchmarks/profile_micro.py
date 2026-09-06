"""Micro-benchmarks for isolated hotspots (2026-08-24 audit)."""
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
data = (ROOT / "tests/data/ECUs/Bosch/ME7.1/022906032CS.bin").read_bytes()
tune = (ROOT / "tests/data/tune/original.bin").read_bytes()

# 1. _is_erased (checksum sweep)
from openremap.core.services.checksums.checksum import _is_erased, _PAGE_SIZES
n = len(data)
chunks = []
for ps in _PAGE_SIZES:
    if n < 2 * ps:
        continue
    for s in range(0, n - n % ps, ps):
        chunks.append(data[s : min(s + ps, n)])
print(f"chunks: {len(chunks)}")
t0 = time.perf_counter()
for _ in range(5):
    s = 0
    for c in chunks:
        s += _is_erased(c)
dt = (time.perf_counter() - t0) / 5
print(f"_is_erased: {len(chunks)} chunks in {dt*1000:.1f}ms")

# 2. extract_raw_strings
from openremap.core.manufacturers.base import BaseManufacturerExtractor
class _B(BaseManufacturerExtractor):
    name = "x"; supported_families = []
    def can_handle(self, data): return True
    def extract(self, data, filename): return {}
b = _B()
reg = slice(0x0000, 0x10000)
t0 = time.perf_counter()
for _ in range(5):
    b.extract_raw_strings(data, reg)
dt = (time.perf_counter() - t0) / 5
print(f"extract_raw_strings (64KB): {dt*1000:.1f}ms")

# 3. count_unique_in_window - full count vs cap-2 on real anchors from a cook
from openremap.core.services.entropy import count_unique_in_window
a = (ROOT / "tests/data/tune/original.bin").read_bytes()
bb = (ROOT / "tests/data/tune/ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL.bin").read_bytes()
from openremap._rust import find_changed_blocks
blocks = find_changed_blocks(a, bb, 16)
print(f"changed blocks: {len(blocks)}")
t0 = time.perf_counter()
tot = 0
for off, size, ob, mb in blocks:
    tot += count_unique_in_window(a, ob, 0, len(a))
dt = time.perf_counter() - t0
print(f"count_unique_in_window full: {len(blocks)} calls in {dt*1000:.1f}ms total={tot}")

def cap2(haystack, needle):
    count = 0; pos = 0
    while True:
        p = haystack.find(needle, pos)
        if p == -1: break
        count += 1
        if count >= 2: return 2
        pos = p + 1
    return count

t0 = time.perf_counter()
tot = 0
for off, size, ob, mb in blocks:
    tot += cap2(a, ob, 0, len(a)) if False else cap2(a, ob)
dt = time.perf_counter() - t0
print(f"cap-at-2 pure python: {len(blocks)} calls in {dt*1000:.1f}ms total={tot}")
