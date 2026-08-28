"""
ISSUE-2 tests — the same-file-only tier.

``cook --allow-non-unique`` stamps recipes with non-unique anchors as
``metadata.portability == "same_file_only"``; ``tune`` refuses to apply
such a recipe to any binary whose sha256 differs from the source unless
``--force`` is passed; ``validate before`` reports the mismatch.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from openremap.core.cli.main import app

runner = CliRunner()


def _make_bin(size: int = 1024, patches: dict | None = None) -> bytes:
    buf = bytearray(size)
    for offset, value in (patches or {}).items():
        if isinstance(value, int):
            buf[offset] = value
        else:
            buf[offset : offset + len(value)] = value
    return bytes(buf)


def _parse_json_from_stdout(stdout: str) -> dict:
    """Extract and parse the first top-level JSON object found in *stdout*."""
    start = stdout.find("{")
    end = stdout.rfind("}") + 1
    assert start != -1, f"No JSON object found in stdout:\n{stdout}"
    return json.loads(stdout[start:end])


def _cook(original: bytes, modified: bytes, tmp_path, name: str = "tune.remap"):
    """Cook via the real CLI; returns the recipe file path."""
    sp = tmp_path / "stock.bin"
    tp = tmp_path / "tuned.bin"
    rp = tmp_path / name
    sp.write_bytes(original)
    tp.write_bytes(modified)
    result = runner.invoke(
        app,
        [
            "cook", str(sp), str(tp),
            "--output", str(rp),
            "--no-annotate-maps",
            "--allow-non-unique",
        ],
    )
    assert result.exit_code == 0, result.output
    return rp


class TestCookStamp:
    def test_non_unique_recipe_gets_same_file_only_stamp(self, tmp_path):
        """Zero-filled fixtures produce non-unique anchors -> stamped."""
        original = _make_bin(1024)
        modified = _make_bin(1024, {100: 0xAA, 200: 0xBB})
        rp = _cook(original, modified, tmp_path)
        data = json.loads(rp.read_text())
        assert data["metadata"]["portability"] == "same_file_only"

    def test_unique_recipe_gets_no_stamp(self, tmp_path):
        """Unique (random-fill) anchors -> no stamp even with the flag."""
        from tests.conftest import make_layout_bin

        original = make_layout_bin(seed=7)
        modified = make_layout_bin(seed=7, map_delta=12)
        rp = _cook(original, modified, tmp_path)
        data = json.loads(rp.read_text())
        assert "portability" not in data.get("metadata", {})


class TestTuneGate:
    def test_same_file_applies_without_force(self, tmp_path):
        original = _make_bin(1024)
        modified = _make_bin(1024, {100: 0xAA})
        rp = _cook(original, modified, tmp_path)

        target = tmp_path / "target.bin"
        target.write_bytes(original)  # the exact source binary
        out = tmp_path / "out.bin"
        result = runner.invoke(
            app, ["tune", str(target), str(rp), "--output", str(out)],
        )
        assert result.exit_code == 0, result.output
        assert out.read_bytes()[100] == 0xAA

    def test_different_file_refused_without_force(self, tmp_path):
        original = _make_bin(1024)
        modified = _make_bin(1024, {100: 0xAA})
        rp = _cook(original, modified, tmp_path)

        other = _make_bin(1024, {500: 0x01})  # different bytes, same size
        target = tmp_path / "target.bin"
        target.write_bytes(other)
        result = runner.invoke(app, ["tune", str(target), str(rp)])
        assert result.exit_code == 1
        assert "SAME-FILE-ONLY" in result.stderr
        assert "--force" in result.stderr

    def test_different_file_applies_with_force(self, tmp_path):
        original = _make_bin(1024)
        modified = _make_bin(1024, {100: 0xAA})
        rp = _cook(original, modified, tmp_path)

        other = _make_bin(1024, {500: 0x01})
        target = tmp_path / "target.bin"
        target.write_bytes(other)
        out = tmp_path / "out.bin"
        result = runner.invoke(
            app, ["tune", str(target), str(rp), "--force", "--output", str(out)],
        )
        assert result.exit_code == 0, result.output
        # loud warning that the guard was overridden
        assert "--force" in result.output
        # the patch still landed (mechanical phases ran and verified)
        assert out.read_bytes()[100] == 0xAA

    def test_unstamped_recipe_not_gated(self, tmp_path):
        """A normal recipe on a different file is not blocked by the gate."""
        from openremap.core.services.recipes.recipe_builder import ECUDiffAnalyzer

        original = _make_bin(1024)
        modified = _make_bin(1024, {100: 0xAA})
        analyzer = ECUDiffAnalyzer(
            original_data=original,
            modified_data=modified,
            original_filename="stock.bin",
            modified_filename="tuned.bin",
            require_unique=False,
        )
        recipe = analyzer.build_recipe()
        rp = tmp_path / "plain.remap"
        rp.write_text(json.dumps(recipe))

        other = _make_bin(1024, {500: 0x01})
        target = tmp_path / "target.bin"
        target.write_bytes(other)
        out = tmp_path / "out.bin"
        result = runner.invoke(
            app, ["tune", str(target), str(rp), "--output", str(out)],
        )
        assert result.exit_code == 0, result.output
        assert out.read_bytes()[100] == 0xAA


class TestValidateGate:
    def test_validate_reports_same_file_only_mismatch(self, tmp_path):
        original = _make_bin(1024)
        modified = _make_bin(1024, {100: 0xAA})
        rp = _cook(original, modified, tmp_path)

        other = _make_bin(1024, {500: 0x01})
        target = tmp_path / "target.bin"
        target.write_bytes(other)
        result = runner.invoke(
            app, ["validate", "before", str(target), str(rp), "--json"],
        )
        assert result.exit_code == 0
        out = _parse_json_from_stdout(result.stdout)
        assert out["same_file_only"]["stamped"] is True
        assert out["same_file_only"]["allowed"] is False
        assert out["summary"]["safe_to_patch"] is False

    def test_validate_same_file_passes_gate(self, tmp_path):
        original = _make_bin(1024)
        modified = _make_bin(1024, {100: 0xAA})
        rp = _cook(original, modified, tmp_path)

        target = tmp_path / "target.bin"
        target.write_bytes(original)
        result = runner.invoke(
            app, ["validate", "before", str(target), str(rp), "--json"],
        )
        assert result.exit_code == 0
        out = _parse_json_from_stdout(result.stdout)
        assert out["same_file_only"]["stamped"] is True
        assert out["same_file_only"]["allowed"] is True
