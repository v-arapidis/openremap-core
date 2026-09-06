"""Break down EDC17 _search cost: C scan vs Python match processing."""
import re, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
data = (ROOT / "tests/data/tune/original.bin").read_bytes()

from openremap.core.manufacturers.bosch.edc17.patterns import PATTERNS, PATTERN_REGIONS, SEARCH_REGIONS

def scan_only(data, pattern):
    hits = 0
    for m in re.finditer(pattern, data):
        hits += 1
        if hits >= 5:
            break
    return hits

def scan_with_processing(data, pattern):
    results = []
    for match in re.finditer(pattern, data):
        value = match.group(0).decode("ascii", errors="ignore").strip()
        if value and value not in results:
            results.append(value)
            if len(results) >= 5:
                break
    return results

for name, pattern in PATTERNS.items():
    region_key = PATTERN_REGIONS.get(name, "full")
    region = SEARCH_REGIONS[region_key]
    sub = data[region] if region.stop is not None else data
    t0 = time.perf_counter()
    h1 = scan_only(sub, pattern)
    t_scan = time.perf_counter() - t0
    t0 = time.perf_counter()
    h2 = scan_with_processing(sub, pattern)
    t_proc = time.perf_counter() - t0
    print(f"{name:26s} region={region_key:14s} scan={t_scan*1000:6.1f}ms proc={t_proc*1000:6.1f}ms hits={h1}")
