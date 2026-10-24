"""
Tests for the ``openremap synth`` command.

The command is the user-facing door to the synthetic generator: it writes a
fixture with no ECU data involved.  These tests cover the file it writes, the
``--json`` ground truth, and the two failure modes (bad parameters, unwritable
destination) — all of which must exit non-zero *and* leave no half-written file.
"""

from __future__ import annotations

import base64
import json

from typer.testing import CliRunner

import openremap.api as api
from openremap.cli.main import app

runner = CliRunner()


def _expected_sha(params: dict) -> str:
    return api.call("generate_dump", params)["sha256"]


class TestSynthCommand:
    def test_writes_the_file_the_api_describes(self, tmp_path):
        out = tmp_path / "demo.bin"
        result = runner.invoke(
            app, ["synth", "-o", str(out), "--size", "65536", "--maps", "2", "--seed", "3"]
        )

        assert result.exit_code == 0, result.output
        assert out.is_file()
        assert len(out.read_bytes()) == 65536
        assert _expected_sha({"size": 65536, "maps": 2, "seed": 3, "layout": ["code", "calibration", "erased", "code"], "cell_width": 2, "byte_order": "little"}) in result.output

    def test_creates_missing_parent_directories(self, tmp_path):
        out = tmp_path / "nested" / "deeper" / "demo.bin"
        result = runner.invoke(app, ["synth", "-o", str(out), "--size", "4096"])

        assert result.exit_code == 0, result.output
        assert out.is_file()

    def test_output_is_deterministic_across_runs(self, tmp_path):
        first, second = tmp_path / "a.bin", tmp_path / "b.bin"
        args = ["--size", "32768", "--maps", "2", "--seed", "11"]
        assert runner.invoke(app, ["synth", "-o", str(first), *args]).exit_code == 0
        assert runner.invoke(app, ["synth", "-o", str(second), *args]).exit_code == 0
        assert first.read_bytes() == second.read_bytes()

    def test_json_prints_ground_truth_without_the_payload(self, tmp_path):
        out = tmp_path / "demo.bin"
        result = runner.invoke(
            app, ["synth", "-o", str(out), "--size", "65536", "--maps", "2", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["size"] == 65536
        assert payload["path"] == str(out)
        assert "data_b64" not in payload, "the JSON view is the ground truth only"
        assert [s["kind"] for s in payload["sectors"]] == [
            "code",
            "calibration",
            "erased",
            "code",
        ]
        assert len(payload["maps"]) == 2

    def test_human_output_names_the_maps_and_the_unknown_caveat(self, tmp_path):
        out = tmp_path / "demo.bin"
        result = runner.invoke(app, ["synth", "-o", str(out), "--maps", "2"])

        assert result.exit_code == 0, result.output
        assert "Planted maps" in result.stdout
        assert "map0" in result.stdout and "map1" in result.stdout
        assert "Unknown" in result.stdout

    def test_defaults_produce_a_256kb_file_with_one_map(self, tmp_path):
        out = tmp_path / "demo.bin"
        result = runner.invoke(app, ["synth", "-o", str(out)])

        assert result.exit_code == 0, result.output
        assert len(out.read_bytes()) == 262144
        assert len(api.call("generate_dump")["maps"]) == 1

    def test_accepts_a_custom_layout(self, tmp_path):
        out = tmp_path / "demo.bin"
        result = runner.invoke(
            app, ["synth", "-o", str(out), "--size", "16384", "--layout", "calibration,erased", "--json"]
        )

        assert result.exit_code == 0, result.output
        assert [s["kind"] for s in json.loads(result.stdout)["sectors"]] == [
            "calibration",
            "erased",
        ]


class TestSynthFailures:
    def test_unknown_sector_kind_exits_one_with_a_message(self, tmp_path):
        out = tmp_path / "demo.bin"
        result = runner.invoke(app, ["synth", "-o", str(out), "--layout", "code,nonsense"])

        assert result.exit_code == 1
        assert "nonsense" in result.output or "layout" in result.output
        assert not out.exists(), "a rejected request must not leave a file behind"

    def test_maps_that_do_not_fit_exit_one(self, tmp_path):
        out = tmp_path / "demo.bin"
        result = runner.invoke(
            app,
            ["synth", "-o", str(out), "--size", "8192", "--layout", "calibration", "--maps", "40"],
        )

        assert result.exit_code == 1
        assert "too small" in result.output
        assert not out.exists()

    def test_unwritable_destination_exits_one(self, tmp_path):
        blocker = tmp_path / "blocked"
        blocker.write_text("not a directory")
        result = runner.invoke(
            app, ["synth", "-o", str(blocker / "demo.bin"), "--size", "4096"]
        )

        assert result.exit_code == 1
        assert "could not write" in result.output
