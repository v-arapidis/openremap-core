"""Profile the audit service (3-way consistency check)."""
import cProfile, io, pstats, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
a = (ROOT / "tests/data/tune/original.bin").read_bytes()
b = (ROOT / "tests/data/tune/ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL.bin").read_bytes()

from openremap.core.services.recipes.recipe_builder import ECUDiffAnalyzer
from openremap.core.services.recipes.audit import audit

recipe = ECUDiffAnalyzer(a, b, "original.bin", "tuned.bin").build_recipe()

t0 = time.perf_counter()
res = audit(a, b, recipe, stock_name="original.bin", tuned_name="tuned.bin", recipe_name="r.remap")
print(f"wall: {time.perf_counter()-t0:.3f}s clean={res.clean} unaccounted={len(res.unaccounted_blocks)}")

pr = cProfile.Profile()
pr.enable()
audit(a, b, recipe, stock_name="original.bin", tuned_name="tuned.bin", recipe_name="r.remap")
pr.disable()
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(25)
print(s.getvalue()[:4000])
