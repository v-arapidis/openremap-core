"""Profile the diff-maps CLI body (Rust scan + Python matching/diff)."""
import cProfile, io, pstats, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
a = (ROOT / "tests/data/tune/original.bin").read_bytes()
b = (ROOT / "tests/data/tune/ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL.bin").read_bytes()

import openremap.core.services.maps.diff_maps as dm

def body():
    stock_result = dm.scan_one(a, slice(0, None), 0.55, 16)
    tuned_result = dm.scan_one(b, slice(0, None), 0.55, 16)
    stock_tables = stock_result["tables"]
    tuned_tables = tuned_result["tables"]
    stock_index = dm._build_stock_index(a, stock_tables)
    matches = []
    used = set()
    for tt in tuned_tables:
        fp = dm._axis_fingerprint(b, tt)
        candidates = stock_index.get(fp, [])
        available = [st for st in candidates if st.offset not in used]
        if not available:
            continue
        best = min(available, key=lambda st: abs(st.offset - tt.offset))
        used.add(best.offset)
        if best.stride is not None:
            so, to = best.offset, tt.offset
            sc = dm._read_cells(a, so, best.cols, best.rows, best.cell_width, best.byte_order, best.stride)
            tc = dm._read_cells(b, to, tt.cols, tt.rows, tt.cell_width, tt.byte_order, tt.stride)
        else:
            sc, tc, so, to = dm._best_alignment(a, b, best.offset, tt.offset, best.cols, best.rows, best.cell_width, best.byte_order)
        diff = dm._diff_cells(sc, tc)
        corr = dm._pearson(sc, tc)
        matches.append((best, tt, diff, corr))
    return len(matches), len(stock_tables), len(tuned_tables)

t0 = time.perf_counter()
n = body()
print(f"wall: {time.perf_counter()-t0:.3f}s matches={n}")

pr = cProfile.Profile()
pr.enable()
body()
pr.disable()
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(20)
print(s.getvalue()[:3500])
