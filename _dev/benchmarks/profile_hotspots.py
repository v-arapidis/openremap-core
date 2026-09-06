"""Profile current CPU hotspots across the main service layer (2026-08-24).

Runs cProfile over the real corpus workloads and prints the top
cumulative-time functions per workload.  Used by the rust-migration audit
to decide what is still Python-hot.

Usage: .venv/bin/python benchmarks/profile_hotspots.py [workload...]
"""
import cProfile
import pstats
import sys
import time

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TUNE_DIR = ROOT / "tests/data/tune"
ME7_DIR = ROOT / "tests/data/ECUs/Bosch/ME7.1"

WORKLOADS = {}


def workload(name):
    def deco(fn):
        WORKLOADS[name] = fn
        return fn
    return deco


@workload("identify-1mb")
def w_identify():
    from openremap.core.services.identify.identifier import identify_ecu
    f = next(ME7_DIR.glob("*.bin"))
    data = f.read_bytes()
    for _ in range(3):
        identify_ecu(data=data, filename=f.name)


@workload("cook-4mb")
def w_cook():
    from openremap.core.services.recipes.recipe_builder import ECUDiffAnalyzer
    a = (TUNE_DIR / "original.bin").read_bytes()
    b = (TUNE_DIR / "ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL.bin").read_bytes()
    ECUDiffAnalyzer(a, b, "original.bin", "tuned.bin").build_recipe()


@workload("scan-vins-4mb")
def w_vins():
    from openremap.core.services.identify.vin_scanner import scan_vins
    data = (TUNE_DIR / "original.bin").read_bytes()
    scan_vins(data, min_confidence=0.0)


@workload("checksum-1mb")
def w_checksum():
    from openremap.core.services.checksums.checksum import sweep
    f = next(ME7_DIR.glob("*.bin"))
    data = f.read_bytes()
    sweep(data)


@workload("layout-4mb")
def w_layout():
    from openremap.core.services.maps.layout import find_ident_blocks, segment
    data = (TUNE_DIR / "original.bin").read_bytes()
    find_ident_blocks(data)
    segment(data)


@workload("ironfelix-1mb")
def w_ironfelix():
    from openremap.core.services.checksums.ironfelix import detect_all
    f = next(ME7_DIR.glob("*.bin"))
    data = f.read_bytes()
    detect_all(data)


@workload("volatile-4mb")
def w_volatile():
    from openremap.core.services.recipes.recipe_builder import ECUDiffAnalyzer
    from openremap.core.services.recipes.volatile import classify_volatile
    a = (TUNE_DIR / "original.bin").read_bytes()
    b = (TUNE_DIR / "ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL.bin").read_bytes()
    recipe = ECUDiffAnalyzer(a, b, "original.bin", "tuned.bin").build_recipe()
    classify_volatile(recipe, a)


@workload("diff-maps-4mb")
def w_diffmaps():
    from openremap.core.services.maps.map_hunter import scan_map_tables, scan_map_axes
    a = (TUNE_DIR / "original.bin").read_bytes()
    b = (TUNE_DIR / "ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL.bin").read_bytes()
    for d in (a, b):
        scan_map_axes(d)
        scan_map_tables(d, min_score=0.55, max_series_tables=16)


@workload("tune-4mb")
def w_tune():
    from openremap.core.services.recipes.recipe_builder import ECUDiffAnalyzer
    from openremap.core.services.recipes.patcher import ECUPatcher
    a = (TUNE_DIR / "original.bin").read_bytes()
    b = (TUNE_DIR / "ALL FILTERS OFF STAGE 1 POWER UP VMAX CANCEL.bin").read_bytes()
    recipe = ECUDiffAnalyzer(a, b, "original.bin", "tuned.bin").build_recipe()
    ECUPatcher(a, recipe).apply_all()


def main():
    names = sys.argv[1:] or list(WORKLOADS)
    for name in names:
        fn = WORKLOADS[name]
        print(f"\n{'=' * 70}\nWORKLOAD: {name}\n{'=' * 70}")
        t0 = time.perf_counter()
        pr = cProfile.Profile()
        pr.enable()
        fn()
        pr.disable()
        wall = time.perf_counter() - t0
        print(f"wall: {wall:.3f}s")
        st = pstats.Stats(pr)
        st.sort_stats("cumulative")
        st.print_stats(25)


if __name__ == "__main__":
    main()
