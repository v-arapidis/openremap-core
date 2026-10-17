"""
Tests for the contribution wizard's API gate.

The gate is the single surface every consumer goes through (CLI, TUI, a host
app), so these tests pin the contract rather than the implementation: paths in,
JSON-safe dict out, and caller mistakes arriving as `InvalidParamsError` instead
of a traceback. The engine itself is covered by
``tests/tuning/services/test_wizard.py``.
"""

from __future__ import annotations

import json

import pytest

import openremap.api as api
from openremap.api import errors

MARKER = b"45/1/SID206/9/5033/DAMOS161/8D0907551"
SIZE = 0x40000


def write_dump(path, *, marker: bytes = MARKER, sw: str = "1037359650"):
    buf = bytearray(b"\xff" * SIZE)
    buf[0x100 : 0x100 + len(marker)] = marker
    ident = f"0261204270 {sw}".encode()
    buf[0x400 : 0x400 + len(ident)] = ident
    path.write_bytes(bytes(buf))
    return path


class TestContributeExtractor:
    def test_returns_a_json_safe_result(self, tmp_path):
        dump = write_dump(tmp_path / "stock.bin")
        result = api.call(
            "contribute_extractor",
            {"paths": [str(dump)], "brand": "Siemens", "family": "SID206"},
        )

        # The transport must be able to serialise it verbatim: no bytes, no
        # dataclasses, no Paths — that is the whole point of the gate.
        json.dumps(result)
        assert result["spec"]["brand"] == "Siemens"
        assert result["spec"]["marker"] == MARKER.decode("latin-1")
        assert result["verification"]["ok"] is True
        assert set(result["artifacts"]) == {"recipe", "extractor", "test", "report"}

    def test_two_dumps_are_measured_together(self, tmp_path):
        first = write_dump(tmp_path / "stock.bin")
        second = write_dump(tmp_path / "tuned.bin", sw="1037359651")
        result = api.call(
            "contribute_extractor",
            {
                "paths": [str(first), str(second)],
                "brand": "Siemens",
                "family": "SID206",
            },
        )
        assert len(result["analysis"]["dumps"]) == 2
        assert "present in all 2 dumps" in " ".join(
            result["analysis"]["candidates"][0]["reasons"]
        )

    def test_hex_marker_override(self, tmp_path):
        dump = write_dump(tmp_path / "stock.bin", marker=b"\xde\xca\xfe\x01")
        result = api.call(
            "contribute_extractor",
            {
                "paths": [str(dump)],
                "brand": "Bosch",
                "family": "M9.9",
                "marker_hex": "decafe01",
            },
        )
        assert result["spec"]["marker_hex"] == "decafe01"

    def test_measure_only_needs_no_checkout(self, tmp_path, monkeypatch):
        import openremap.core.services.wizard.fixtures as fixtures

        monkeypatch.setattr(fixtures, "find_recipes_dir", lambda start=None: None)
        fixtures.reset_cache()
        try:
            dump = write_dump(tmp_path / "stock.bin")
            result = api.call(
                "contribute_extractor",
                {
                    "paths": [str(dump)],
                    "brand": "Siemens",
                    "family": "SID206",
                    "measure_only": True,
                },
            )
            json.dumps(result)
            assert result["mode"] == "measure"
            assert result["analysis"]["recommended"] is None
        finally:
            fixtures.reset_cache()

    def test_measure_only_without_names(self, tmp_path, monkeypatch):
        import openremap.core.services.wizard.fixtures as fixtures

        monkeypatch.setattr(fixtures, "find_recipes_dir", lambda start=None: None)
        fixtures.reset_cache()
        try:
            dump = write_dump(tmp_path / "stock.bin")
            result = api.call(
                "contribute_extractor", {"paths": [str(dump)], "measure_only": True}
            )
            assert result["mode"] == "measure"
        finally:
            fixtures.reset_cache()

    def test_generating_requires_the_names(self, tmp_path):
        dump = write_dump(tmp_path / "stock.bin")
        with pytest.raises(errors.InvalidParamsError, match="brand and family"):
            api.call("contribute_extractor", {"paths": [str(dump)]})

    def test_strict_mode_refuses_without_a_checkout(self, tmp_path, monkeypatch):
        import openremap.core.services.wizard.fixtures as fixtures

        monkeypatch.setattr(fixtures, "find_recipes_dir", lambda start=None: None)
        fixtures.reset_cache()
        try:
            dump = write_dump(tmp_path / "stock.bin")
            with pytest.raises(errors.InvalidParamsError, match="measure_only"):
                api.call(
                    "contribute_extractor",
                    {
                        "paths": [str(dump)],
                        "brand": "Siemens",
                        "family": "SID206",
                    },
                )
        finally:
            fixtures.reset_cache()

    def test_missing_path_is_a_caller_error(self):
        with pytest.raises(errors.InvalidParamsError):
            api.call(
                "contribute_extractor",
                {
                    "paths": ["/nonexistent/dump.bin"],
                    "brand": "Siemens",
                    "family": "SID206",
                },
            )

    def test_no_paths_is_a_caller_error(self):
        with pytest.raises(errors.InvalidParamsError):
            api.call(
                "contribute_extractor",
                {"paths": [], "brand": "Siemens", "family": "SID206"},
            )

    def test_bad_identifier_is_a_caller_error(self, tmp_path):
        dump = write_dump(tmp_path / "stock.bin")
        with pytest.raises(errors.InvalidParamsError, match="invalid family"):
            api.call(
                "contribute_extractor",
                {"paths": [str(dump)], "brand": "Siemens", "family": "../../etc/passwd"},
            )

    def test_both_marker_forms_at_once_is_a_caller_error(self, tmp_path):
        dump = write_dump(tmp_path / "stock.bin")
        with pytest.raises(errors.InvalidParamsError):
            api.call(
                "contribute_extractor",
                {
                    "paths": [str(dump)],
                    "brand": "Siemens",
                    "family": "SID206",
                    "marker": "X",
                    "marker_hex": "58",
                },
            )


class TestContributeCheck:
    def test_checks_a_shipped_extractor(self):
        result = api.call(
            "contribute_check",
            {"path": "openremap/core/manufacturers/siemens/sid803/extractor.py"},
        )
        json.dumps(result)
        assert result["ok"] is True
        assert result["family"] == "SID803"
        assert any(c["name"] == "steals no other family" for c in result["checks"])

    def test_missing_file_is_a_caller_error(self):
        with pytest.raises(errors.InvalidParamsError):
            api.call("contribute_check", {"path": "/nonexistent/extractor.py"})


def test_both_methods_are_discoverable():
    """A host finds the wizard through introspection, like every other method."""
    names = {m["name"] for m in api.call("list_methods")["methods"]}
    assert {"contribute_extractor", "contribute_check"} <= names
