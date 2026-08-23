"""
Tests for the map content classifier (map_classifier.py).

Covers:
  - axis profile scoring: RPM/load/pressure/speed-shaped axes
  - surface-shape labels: fuel / timing / boost / torque
  - family fuel-type gating: diesel has no timing, petrol no duration
  - unknown label for low-signal tables
  - classify_tables: dict keyed by data offset
"""

from __future__ import annotations

import struct

import pytest

from openremap.core.services.maps.map_classifier import (
    classify_table,
    classify_tables,
    family_fuel_type,
)
from openremap.core.services.maps.map_hunter import MapTable

RPM = [500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 5500, 6000]
LOAD = [10, 20, 30, 40, 50, 60, 70, 80]


def _build(x, y, fn, base=0x100) -> tuple[bytes, int]:
    buf = bytearray(4096)
    o = base
    buf[o : o + 2 * len(x)] = struct.pack(f"<{len(x)}H", *x)
    o += 2 * len(x)
    buf[o : o + 2 * len(y)] = struct.pack(f"<{len(y)}H", *y)
    o += 2 * len(y)
    off = o
    for yi in range(len(y)):
        for xi in range(len(x)):
            v = max(0, min(65535, fn(xi, yi)))
            struct.pack_into("<H", buf, o, v)
            o += 2
    return bytes(buf), off


def _table(data_len: int, off: int, cols: int, rows: int, xo=0x100, yo=0x118):
    return MapTable(off, cols, rows, 2, "little", xo, yo, 0.9, None)


class TestFamilyFuelType:
    def test_diesel_families(self):
        assert family_fuel_type("EDC16") == "diesel"
        assert family_fuel_type("EDC17C46") == "diesel"
        assert family_fuel_type("PPD1.2") == "diesel"

    def test_petrol_families(self):
        assert family_fuel_type("ME7.5.5") == "petrol"
        assert family_fuel_type("M3.8.3") == "petrol"
        assert family_fuel_type("IAW 4LV") == "petrol"

    def test_unknown(self):
        assert family_fuel_type(None) is None
        assert family_fuel_type("WeirdFamily") is None


class TestSurfaceLabels:
    def test_fuel_surface(self):
        data, off = _build(RPM, LOAD, lambda xi, yi: 200 + yi * 40 - xi * 3)
        label, conf = classify_table(data, _table(0, off, 12, 8))
        assert label == "fuel"
        assert conf >= 0.6

    def test_timing_surface(self):
        data, off = _build(RPM, LOAD, lambda xi, yi: 800 - yi * 30 + xi * 5)
        label, conf = classify_table(data, _table(0, off, 12, 8))
        assert label == "timing"
        assert conf >= 0.6

    def test_boost_surface(self):
        data, off = _build(RPM, LOAD, lambda xi, yi: 500 + yi * 25 + xi * 8)
        label, _conf = classify_table(data, _table(0, off, 12, 8))
        assert label == "boost"

    def test_torque_1d_plateau(self):
        x1 = [800, 1200, 1600, 2000, 2400, 2800, 3200]
        buf = bytearray(256)
        buf[0:14] = struct.pack("<7H", *x1)
        vals = [300, 320, 340, 350, 350, 350, 350]
        buf[14:28] = struct.pack("<7H", *vals)
        t = MapTable(14, 7, 1, 2, "little", 0, None, 0.9, None)
        label, conf = classify_table(bytes(buf), t)
        assert label == "torque"
        assert conf >= 0.6

    def test_diesel_gates_timing(self):
        data, off = _build(RPM, LOAD, lambda xi, yi: 800 - yi * 30 + xi * 5)
        label, _conf = classify_table(
            data, _table(0, off, 12, 8), fuel_type="diesel",
        )
        assert label != "timing"

    def test_petrol_gates_duration(self):
        data, off = _build(RPM, LOAD, lambda xi, yi: 200 + yi * 40 - xi * 3)
        label, _conf = classify_table(
            data, _table(0, off, 12, 8), fuel_type="petrol",
        )
        assert label != "duration"

    def test_small_table_unknown(self):
        x = [100, 200, 300, 400]
        y = [10, 20, 30, 40]
        data, off = _build(x, y, lambda xi, yi: 100 + (xi * yi) % 50, base=0x100)
        t = MapTable(off, 4, 4, 2, "little", 0x100, 0x108, 0.9, None)
        label, conf = classify_table(data, t)
        assert label in ("unknown", "fuel", "timing", "boost", "torque", "duration")
        assert 0.0 <= conf <= 1.0


class TestClassifyTables:
    def test_keyed_by_offset(self):
        x = [100, 200, 300, 400, 500, 600]
        y = [10, 20, 30, 40]
        data, off = _build(x, y, lambda xi, yi: 200 + yi * 30 - xi * 2, base=0x100)
        tables = [MapTable(off, 6, 4, 2, "little", 0x100, 0x10C, 0.9, None)]
        result = classify_tables(data, tables, fuel_type="petrol")
        assert off in result
        label, conf = result[off]
        assert isinstance(label, str)
        assert 0.0 <= conf <= 1.0
