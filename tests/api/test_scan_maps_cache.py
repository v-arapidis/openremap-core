"""
Two-stage ``scan_maps`` contract: schema, cache keys, container decoding.

Guards the three regressions behind audit item 11c:

* the four axis knobs are **declared** in ``scan_map_tables``' schema — the
  fallback branch read them while no consumer could discover or validate them;
* both caches are keyed on the params as well as the file — keyed on the file
  alone, a second call with different thresholds was served the first call's
  answer (reproduced live: ``min_table_score`` 0.55 then 0.99 returned the same
  120 tables off a warm cache);
* both stages load through :func:`openremap.api._io.load_binary`, so Intel HEX /
  S-Record containers decode exactly as they do in every other command.

The Rust-backed cores are replaced with recorders: this is a contract test for
the gate's plumbing, not for map detection (that is covered by the corpus tests).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import openremap.api as api
from openremap.api.commands import scan_maps as scan_maps_cmd

AXIS_PARAMS = ("min_axis_length", "max_axis_length", "min_step", "max_step")
DEFAULTS = {
    "min_axis_length": 4,
    "max_axis_length": 32,
    "min_step": 1,
    "max_step": 10000,
}


@pytest.fixture(autouse=True)
def _clear_caches():
    """The two-stage caches are module-level — keep tests independent."""
    scan_maps_cmd._AXES_CACHE.clear()
    scan_maps_cmd._TABLES_CACHE.clear()
    yield
    scan_maps_cmd._AXES_CACHE.clear()
    scan_maps_cmd._TABLES_CACHE.clear()


@pytest.fixture
def stub_cores(monkeypatch):
    """Replace the map-hunting cores with recorders of what they were asked."""
    calls = {"axes": [], "tables": []}
    axes = [SimpleNamespace(offset=16, length=8, byte_order="big", values=[1, 2])]
    tables = [
        SimpleNamespace(
            offset=16,
            cols=2,
            rows=2,
            cell_width=1,
            byte_order="big",
            x_axis_offset=0,
            y_axis_offset=8,
            stride=1,
            score=0.9,
        )
    ]

    def fake_scan_map_axes(data, **kwargs):
        calls["axes"].append(kwargs)
        return axes

    def fake_scan_map_tables(data, *, axes, **kwargs):
        calls["tables"].append(kwargs)
        return tables

    monkeypatch.setattr(scan_maps_cmd, "scan_map_axes", fake_scan_map_axes)
    monkeypatch.setattr(scan_maps_cmd, "scan_map_tables", fake_scan_map_tables)
    return calls


@pytest.fixture
def binary(tmp_path):
    p = tmp_path / "dump.bin"
    p.write_bytes(b"\x00" * 128)
    return str(p)


# --- schema ------------------------------------------------------------------


def test_axis_params_declared_in_scan_map_tables_schema():
    meta = next(m for m in api.list_methods() if m["name"] == "scan_map_tables")
    for name in AXIS_PARAMS:
        assert name in meta["input_schema"], f"{name} is undeclared"
        assert meta["input_schema"][name]["default"] == DEFAULTS[name]


def test_declared_axis_params_reach_the_core(stub_cores, binary):
    api.call("scan_map_tables", {"path": binary, "min_axis_length": 6, "max_step": 500})
    assert stub_cores["axes"] == [
        {"min_axis_length": 6, "max_axis_length": 32, "min_step": 1, "max_step": 500}
    ]


# --- cache keys --------------------------------------------------------------


def test_changed_table_score_is_not_served_from_cache(stub_cores, binary):
    api.call("scan_map_tables", {"path": binary, "min_table_score": 0.55})
    api.call("scan_map_tables", {"path": binary, "min_table_score": 0.99})
    assert [c["min_score"] for c in stub_cores["tables"]] == [0.55, 0.99]


def test_changed_axis_params_recompute(stub_cores, binary):
    api.call("scan_map_tables", {"path": binary})
    api.call("scan_map_tables", {"path": binary, "min_axis_length": 12})
    assert [c["min_axis_length"] for c in stub_cores["axes"]] == [4, 12]


def test_identical_params_are_served_from_cache(stub_cores, binary):
    first = api.call("scan_map_tables", {"path": binary})
    second = api.call("scan_map_tables", {"path": binary})
    assert first == second
    assert len(stub_cores["tables"]) == 1, "identical request should hit the cache"


def test_stage2_reuses_stage1_axes(stub_cores, binary):
    api.call("scan_map_axes", {"path": binary})
    api.call("scan_map_tables", {"path": binary})
    assert len(stub_cores["axes"]) == 1, "stage 2 must reuse stage 1's axes"
    assert len(stub_cores["tables"]) == 1


# --- container decoding ------------------------------------------------------


def test_both_stages_load_through_load_binary(stub_cores, monkeypatch, binary):
    seen = []

    def fake_load_binary(path):
        seen.append(path)
        return b"\x00" * 128

    monkeypatch.setattr(scan_maps_cmd, "load_binary", fake_load_binary)

    api.call("scan_map_axes", {"path": binary})
    api.call("scan_map_tables", {"path": binary})  # reuses the axes cache
    api.call("scan_map_tables", {"path": binary, "min_axis_length": 12})  # re-detects

    assert seen == [binary, binary], "each stage must decode via load_binary"
