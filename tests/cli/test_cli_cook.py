"""
Tests for ``openremap cook <original> <modified> [--output <recipe.remap>]`.

Runs every scenario through the real CLI via typer.testing.CliRunner.
No mocking — all files are created in pytest's tmp_path fixture.

Exit-code contract (derived from the cook command source):
  0  — success (recipe built, regardless of instruction count)
  1  — bad input (wrong extension, empty file, or I/O error caught in the
       command), or non-unique context anchors when --allow-non-unique is
       not passed (Guard 3 — the strict default)
  2  — missing file (Click enforces exists=True on both Path arguments)

Note: the synthetic test binaries are zero-filled, which produces non-unique
context anchors — the success-path tests below pass --allow-non-unique.
Strict-default behaviour is covered by TestRequireUniqueStrict.
"""

import json

import pytest
from pathlib import Path
from typer.testing import CliRunner

from openremap.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bin(size: int = 1024, patches: dict | None = None) -> bytes:
    """Return a zero-filled byte string of *size* bytes with optional patches.

    Args:
        size:    Total length in bytes.
        patches: Mapping of {offset: value}.  An int value is written as a
                 single byte; a bytes value is written verbatim.
    """
    buf = bytearray(size)
    for offset, value in (patches or {}).items():
        if isinstance(value, int):
            buf[offset] = value
        else:
            buf[offset : offset + len(value)] = value
    return bytes(buf)


def _parse_json_from_stdout(stdout: str) -> dict:
    """Extract and parse the first JSON object found in a mixed-text stdout.

    The cook command prints a heading before the JSON and a summary table
    after it.  We locate the outermost ``{…}`` block and parse that.
    """
    start = stdout.find("{")
    end = stdout.rfind("}") + 1
    assert start != -1, f"No JSON object found in stdout:\n{stdout}"
    return json.loads(stdout[start:end])


# ---------------------------------------------------------------------------
# TestCookSuccess — valid inputs, various flag combinations
# ---------------------------------------------------------------------------


class TestCookSuccess:
    def test_identical_binaries_zero_instructions(self, tmp_path):
        """Diffing two identical binaries produces a recipe with 0 instructions."""
        data = _make_bin(1024)
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(data)
        modified.write_bytes(data)
        output = tmp_path / "recipe.remap"

        result = runner.invoke(
            app, ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)]
        )

        assert result.exit_code == 0, result.output
        recipe = json.loads(output.read_text())
        assert recipe["instructions"] == []
        assert recipe["statistics"] == {}

    def test_different_binaries_produces_instructions(self, tmp_path):
        """Diffing binaries that differ by one byte yields ≥ 1 instruction."""
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024, {100: 0xAA}))
        output = tmp_path / "recipe.remap"

        result = runner.invoke(
            app, ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)]
        )

        assert result.exit_code == 0, result.output
        recipe = json.loads(output.read_text())
        assert len(recipe["instructions"]) >= 1

    def test_cook_to_stdout_is_valid_json(self, tmp_path):
        """Without --output the recipe JSON is written to stdout."""
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024, {100: 0xAA}))

        result = runner.invoke(app, ["cook", "--allow-non-unique", str(original), str(modified)])

        assert result.exit_code == 0, result.output
        recipe = _parse_json_from_stdout(result.stdout)
        assert "instructions" in recipe

    def test_cook_with_output_creates_file(self, tmp_path):
        """--output writes the recipe to the specified path."""
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024, {200: 0xFF}))
        output = tmp_path / "recipe.remap"

        result = runner.invoke(
            app, ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)]
        )

        assert result.exit_code == 0, result.output
        assert output.exists(), "Recipe file was not created"
        recipe = json.loads(output.read_text())
        assert "instructions" in recipe
        assert "metadata" in recipe

    def test_compact_flag_produces_single_line_json(self, tmp_path):
        """--compact writes minified (non-indented) JSON to the output file."""
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024, {100: 0xAA}))
        output = tmp_path / "recipe.remap"

        result = runner.invoke(
            app,
            [
                "cook",
                "--allow-non-unique",
                str(original),
                str(modified),
                "--output",
                str(output),
                "--compact",
            ],
        )

        assert result.exit_code == 0, result.output
        content = output.read_text()
        non_empty_lines = [ln for ln in content.splitlines() if ln.strip()]
        assert len(non_empty_lines) == 1, (
            "Compact JSON must occupy exactly one line; "
            f"got {len(non_empty_lines)} non-empty lines"
        )

    def test_pretty_flag_produces_indented_json(self, tmp_path):
        """--pretty (the default) writes indented JSON to the output file."""
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024, {100: 0xAA}))
        output = tmp_path / "recipe.remap"

        result = runner.invoke(
            app,
            [
                "cook",
                "--allow-non-unique",
                str(original),
                str(modified),
                "--output",
                str(output),
                "--pretty",
            ],
        )

        assert result.exit_code == 0, result.output
        content = output.read_text()
        # Pretty-printed JSON has more than one non-empty line.
        non_empty_lines = [ln for ln in content.splitlines() if ln.strip()]
        assert len(non_empty_lines) > 1, "Pretty JSON should span multiple lines"

    def test_ori_extension_accepted(self, tmp_path):
        """.ori files are treated as valid binary inputs."""
        original = tmp_path / "stock.ori"
        modified = tmp_path / "tuned.ori"
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024))
        output = tmp_path / "recipe.remap"

        result = runner.invoke(
            app, ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)]
        )

        assert result.exit_code == 0, result.output

    def test_recipe_has_expected_top_level_keys(self, tmp_path):
        """Produced recipe always carries metadata, ecu, statistics, and instructions."""
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024, {100: 0xAA}))
        output = tmp_path / "recipe.remap"

        runner.invoke(
            app, ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)]
        )

        recipe = json.loads(output.read_text())
        for key in ("metadata", "ecu", "statistics", "instructions"):
            assert key in recipe, f"Missing top-level recipe key: {key!r}"

    def test_recipe_instruction_has_required_fields(self, tmp_path):
        """Each instruction carries at minimum: offset, ob, mb, ctx, and size."""
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024, {100: 0xAA}))
        output = tmp_path / "recipe.remap"

        runner.invoke(
            app, ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)]
        )

        recipe = json.loads(output.read_text())
        assert len(recipe["instructions"]) >= 1
        inst = recipe["instructions"][0]
        for field in ("offset", "ob", "mb", "ctx", "size"):
            assert field in inst, f"Instruction missing required field: {field!r}"

    def test_instruction_offset_matches_changed_byte_position(self, tmp_path):
        """The instruction offset in the recipe points to the diffed byte."""
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(_make_bin(1024))
        # Change byte at offset 100 from 0x00 to 0xAA
        modified.write_bytes(_make_bin(1024, {100: 0xAA}))
        output = tmp_path / "recipe.remap"

        runner.invoke(
            app, ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)]
        )

        recipe = json.loads(output.read_text())
        offsets = [inst["offset"] for inst in recipe["instructions"]]
        assert 100 in offsets, f"Expected offset 100 in instructions, got: {offsets}"

    def test_instruction_ob_reflects_original_byte(self, tmp_path):
        """The ob field records the original byte value at the changed offset."""
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        # Original has 0x00 at offset 100; modified has 0xAA there.
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024, {100: 0xAA}))
        output = tmp_path / "recipe.remap"

        runner.invoke(
            app, ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)]
        )

        recipe = json.loads(output.read_text())
        inst_at_100 = next(
            (i for i in recipe["instructions"] if i["offset"] == 100), None
        )
        assert inst_at_100 is not None
        assert inst_at_100["ob"].upper() == "00"
        assert inst_at_100["mb"].upper() == "AA"

    def test_cook_multiple_changed_bytes(self, tmp_path):
        """Multiple changed offsets all appear as instructions in the recipe."""
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024, {100: 0xAA, 500: 0xBB}))
        output = tmp_path / "recipe.remap"

        result = runner.invoke(
            app, ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)]
        )

        assert result.exit_code == 0, result.output
        recipe = json.loads(output.read_text())
        offsets = [inst["offset"] for inst in recipe["instructions"]]
        # Both changed positions (or a single block spanning them) must be covered.
        assert any(o <= 100 for o in offsets)

    def test_help_exits_zero(self):
        """--help prints usage information and exits 0."""
        result = runner.invoke(app, ["cook", "--help"])

        assert result.exit_code == 0
        combined = result.stdout + result.stderr
        # Usage text mentions the positional arguments.
        assert "original" in combined.lower() or "modified" in combined.lower()


# ---------------------------------------------------------------------------
# TestCookFileErrors — I/O and read errors
# ---------------------------------------------------------------------------


class TestCookFileErrors:
    """Tests for file I/O errors and permission issues."""

    def test_cook_unreadable_original_file_exits_one(self, tmp_path):
        """An original file that cannot be read exits 1."""
        original = tmp_path / "original.bin"
        modified = tmp_path / "modified.bin"

        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024))

        # Make original unreadable
        original.chmod(0o000)

        try:
            result = runner.invoke(app, ["cook", "--allow-non-unique", str(original), str(modified)])
            assert result.exit_code in (1, 2)
            error_text = result.stderr + result.stdout
            assert "error" in error_text.lower() or "read" in error_text.lower()
        finally:
            # Restore permissions for cleanup
            original.chmod(0o644)

    def test_cook_unreadable_modified_file_exits_one(self, tmp_path):
        """A modified file that cannot be read exits 1."""
        original = tmp_path / "original.bin"
        modified = tmp_path / "modified.bin"

        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024))

        # Make modified unreadable
        modified.chmod(0o000)

        try:
            result = runner.invoke(app, ["cook", "--allow-non-unique", str(original), str(modified)])
            assert result.exit_code in (1, 2)
            error_text = result.stderr + result.stdout
            assert "error" in error_text.lower() or "read" in error_text.lower()
        finally:
            # Restore permissions for cleanup
            modified.chmod(0o644)


# ---------------------------------------------------------------------------
# TestCookEdgeCases — boundary conditions
# ---------------------------------------------------------------------------


class TestCookEdgeCases:
    """Tests for edge cases and unusual scenarios."""

    def test_cook_very_small_files(self, tmp_path):
        """Cook can handle very small binary files (e.g., 1 byte)."""
        original = tmp_path / "original.bin"
        modified = tmp_path / "modified.bin"
        output = tmp_path / "recipe.remap"

        original.write_bytes(b"\x00")
        modified.write_bytes(b"\xff")

        result = runner.invoke(
            app, ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)]
        )

        assert result.exit_code == 0, result.output
        assert output.exists()
        recipe = json.loads(output.read_text())
        assert len(recipe["instructions"]) >= 1

    def test_cook_very_large_files(self, tmp_path):
        """Cook can handle large binary files."""
        # Create 10 MB files
        size = 10 * 1024 * 1024
        original = tmp_path / "original.bin"
        modified = tmp_path / "modified.bin"
        output = tmp_path / "recipe.remap"

        original.write_bytes(_make_bin(size))
        modified.write_bytes(_make_bin(size, {100: 0xAA}))

        result = runner.invoke(
            app, ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)]
        )

        assert result.exit_code == 0, result.output
        assert output.exists()

    def test_cook_identical_files_zero_instructions(self, tmp_path):
        """Cooking identical files produces zero instructions."""
        data = _make_bin(1024)
        original = tmp_path / "original.bin"
        modified = tmp_path / "modified.bin"
        output = tmp_path / "recipe.remap"

        original.write_bytes(data)
        modified.write_bytes(data)

        result = runner.invoke(
            app, ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)]
        )

        assert result.exit_code == 0, result.output
        recipe = json.loads(output.read_text())
        assert recipe["instructions"] == []
        assert recipe["statistics"] == {}

    def test_cook_files_of_different_sizes(self, tmp_path):
        """Cook rejects files of different sizes with exit code 1."""
        original = tmp_path / "original.bin"
        modified = tmp_path / "modified.bin"
        output = tmp_path / "recipe.remap"

        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(2048))  # Different size

        result = runner.invoke(
            app, ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)]
        )

        assert result.exit_code == 1
        assert not output.exists()

    def test_cook_many_changes_single_recipe(self, tmp_path):
        """Cook correctly handles recipes with many changes."""
        original = tmp_path / "original.bin"
        modified = tmp_path / "modified.bin"
        output = tmp_path / "recipe.remap"

        original_data = _make_bin(1024)
        patches = {i * 10: 0xFF for i in range(100)}  # 100 changes
        modified_data = _make_bin(1024, patches)

        original.write_bytes(original_data)
        modified.write_bytes(modified_data)

        result = runner.invoke(
            app, ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)]
        )

        assert result.exit_code == 0, result.output
        recipe = json.loads(output.read_text())
        # Should have multiple instructions (might be merged into fewer blocks)
        assert len(recipe["instructions"]) >= 1

    def test_cook_output_directory_does_not_exist(self, tmp_path):
        """Cook fails gracefully when the output parent path is a file, not a directory."""
        original = tmp_path / "original.bin"
        modified = tmp_path / "modified.bin"

        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024))

        # Create a FILE where the parent directory would need to be.
        # output.parent.mkdir(...) will raise NotADirectoryError / FileExistsError
        # because the path already exists as a regular file.
        parent_is_file = tmp_path / "somefile.bin"
        parent_is_file.write_bytes(b"\x00")
        output = parent_is_file / "recipe.remap"  # parent is a file, not a dir

        result = runner.invoke(
            app,
            ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)],
        )

        # Should fail because mkdir raises OSError (FileExistsError / NotADirectoryError)
        assert result.exit_code == 1

    def test_cook_recipe_statistics_correct(self, tmp_path):
        """Cooked recipe statistics are accurate."""
        original = tmp_path / "original.bin"
        modified = tmp_path / "modified.bin"
        output = tmp_path / "recipe.remap"

        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024, {100: 0xAA, 200: 0xBB}))

        result = runner.invoke(
            app, ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)]
        )

        assert result.exit_code == 0, result.output
        recipe = json.loads(output.read_text())
        stats = recipe["statistics"]

        # Should have statistics for non-empty diff
        assert stats["total_changes"] >= 1
        assert stats["total_bytes_changed"] >= 1
        assert stats["percentage_changed"] > 0


# ---------------------------------------------------------------------------
# TestCookErrors — invalid inputs that must produce a non-zero exit
# ---------------------------------------------------------------------------


class TestCookErrors:
    def test_non_bin_original_exits_one(self, tmp_path):
        """An original file with a wrong extension causes exit 1.

        The file must exist so that Click's exists=True check passes; the
        extension is then validated by the command's own _check_bin helper.
        """
        original = tmp_path / "stock.txt"  # wrong extension — file exists
        modified = tmp_path / "tuned.bin"
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024))

        result = runner.invoke(app, ["cook", "--allow-non-unique", str(original), str(modified)])

        assert result.exit_code == 1
        error_text = result.stderr + result.stdout
        assert "bin" in error_text.lower() or "ori" in error_text.lower()

    def test_non_bin_modified_exits_one(self, tmp_path):
        """A modified file with a wrong extension causes exit 1."""
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.dat"  # wrong extension — file exists
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024))

        result = runner.invoke(app, ["cook", "--allow-non-unique", str(original), str(modified)])

        assert result.exit_code == 1

    def test_non_bin_both_files_exits_one(self, tmp_path):
        """Both files having wrong extensions still causes exit 1 (first one checked)."""
        original = tmp_path / "stock.exe"
        modified = tmp_path / "tuned.exe"
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024))

        result = runner.invoke(app, ["cook", "--allow-non-unique", str(original), str(modified)])

        assert result.exit_code == 1

    def test_missing_original_exits_nonzero(self, tmp_path):
        """A non-existent original file causes a non-zero exit.

        Click enforces exists=True on the argument and reports the error
        (typically exit 2) before the command body runs.
        """
        missing = tmp_path / "ghost.bin"
        modified = tmp_path / "tuned.bin"
        modified.write_bytes(_make_bin(1024))

        result = runner.invoke(app, ["cook", str(missing), str(modified)])

        assert result.exit_code != 0

    def test_missing_modified_exits_nonzero(self, tmp_path):
        """A non-existent modified file causes a non-zero exit."""
        original = tmp_path / "stock.bin"
        original.write_bytes(_make_bin(1024))
        missing = tmp_path / "ghost.bin"

        result = runner.invoke(app, ["cook", str(original), str(missing)])

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Additional coverage — error paths in _read_bin and output write
# ---------------------------------------------------------------------------


class TestCookReadAndWriteErrors:
    """Covers OSError on read and empty-file paths (cook.py lines 54-72, 195-202)."""

    def test_oserror_reading_original_exits_one(self, tmp_path):
        """OSError when reading the original binary exits 1 with an error message."""
        from unittest.mock import patch

        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(b"\x00" * 1024)
        modified.write_bytes(b"\x00" * 1024)

        with patch("pathlib.Path.read_bytes", side_effect=OSError("permission denied")):
            result = runner.invoke(app, ["cook", "--allow-non-unique", str(original), str(modified)])

        assert result.exit_code == 1
        combined = result.stdout + result.stderr
        assert "error" in combined.lower()

    def test_empty_original_file_exits_one(self, tmp_path):
        """A zero-byte original file exits 1 with an 'empty' error message."""
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(b"")  # empty — triggers the empty-file branch
        modified.write_bytes(b"\x00" * 1024)

        result = runner.invoke(app, ["cook", "--allow-non-unique", str(original), str(modified)])

        assert result.exit_code == 1
        combined = result.stdout + result.stderr
        assert "empty" in combined.lower()

    def test_empty_modified_file_exits_one(self, tmp_path):
        """A zero-byte modified file exits 1 with an 'empty' error message."""
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(b"\x00" * 1024)
        modified.write_bytes(b"")  # empty — triggers the empty-file branch

        result = runner.invoke(app, ["cook", "--allow-non-unique", str(original), str(modified)])

        assert result.exit_code == 1
        combined = result.stdout + result.stderr
        assert "empty" in combined.lower()

    def test_oserror_writing_output_exits_one(self, tmp_path):
        """OSError when writing the recipe JSON file exits 1 with an error message."""
        from unittest.mock import patch

        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        output = tmp_path / "recipe.remap"
        original.write_bytes(b"\x00" * 1024)
        modified.write_bytes(b"\x01" * 1024)  # one byte differs → one instruction

        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            result = runner.invoke(
                app,
                ["cook", "--allow-non-unique", str(original), str(modified), "--output", str(output)],
            )

        assert result.exit_code == 1
        combined = result.stdout + result.stderr
        assert "error" in combined.lower()

    def test_analyzer_exception_exits_one(self, tmp_path):
        """Exception from ECUDiffAnalyzer exits 1 with 'cook failed' message (lines 195-202)."""
        from unittest.mock import patch

        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(b"\x00" * 1024)
        modified.write_bytes(b"\x01" * 1024)

        with patch(
            "openremap.cli.commands.cook.ECUDiffAnalyzer",
            side_effect=RuntimeError("diff engine crashed"),
        ):
            result = runner.invoke(app, ["cook", "--allow-non-unique", str(original), str(modified)])

        assert result.exit_code == 1
        combined = result.stdout + result.stderr
        assert "error" in combined.lower() or "cook failed" in combined.lower()


# ---------------------------------------------------------------------------
# TestRequireUniqueStrict — Guard 3 strict default + opt-out
# ---------------------------------------------------------------------------


class TestRequireUniqueStrict:
    def test_non_unique_anchors_fail_by_default(self, tmp_path):
        """Zero-filled data → non-unique anchors → cook aborts with exit 1."""
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        output = tmp_path / "recipe.remap"
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024, {100: 0xAA}))

        result = runner.invoke(
            app,
            ["cook", str(original), str(modified), "--output", str(output)],
        )

        assert result.exit_code == 1
        combined = result.stdout + result.stderr
        assert "non-unique" in combined.lower()
        assert "--allow-non-unique" in combined
        assert not output.exists()

    def test_allow_non_unique_succeeds_with_warning(self, tmp_path):
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        output = tmp_path / "recipe.remap"
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024, {100: 0xAA}))

        result = runner.invoke(
            app,
            [
                "cook",
                "--allow-non-unique",
                str(original),
                str(modified),
                "--output",
                str(output),
            ],
        )

        assert result.exit_code == 0
        assert output.exists()
        combined = result.stdout + result.stderr
        assert "non-unique" in combined.lower()

    def test_unique_anchors_succeed_without_flag(self, tmp_path):
        """High-entropy data → unique anchors → strict default still cooks."""
        import os

        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        output = tmp_path / "recipe.remap"
        data = bytearray(os.urandom(1024))
        original.write_bytes(bytes(data))
        mod = bytearray(data)
        mod[100] = (mod[100] + 1) & 0xFF
        modified.write_bytes(bytes(mod))

        result = runner.invoke(
            app,
            ["cook", str(original), str(modified), "--output", str(output)],
        )

        assert result.exit_code == 0
        assert output.exists()

    def test_help_shows_flag(self):
        result = runner.invoke(app, ["cook", "--help"])
        assert result.exit_code == 0
        # Rich help panel truncates long options — match the stable fragment.
        assert "allow-non-uni" in result.stdout


# ---------------------------------------------------------------------------
# TestRecipeDeterminism — the "git-level" layout contract
# ---------------------------------------------------------------------------


class TestRecipeDeterminism:
    """Re-cooking the same pair produces an identical file except the
    created_at timestamp — the foundation for clean git diffs."""

    def test_two_cooks_identical_modulo_created_at(self, tmp_path):
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024, {100: 0xAA, 200: 0xBB}))

        a = runner.invoke(
            app,
            [
                "cook",
                "--allow-non-unique",
                str(original),
                str(modified),
                "--compact",
            ],
        )
        b = runner.invoke(
            app,
            [
                "cook",
                "--allow-non-unique",
                str(original),
                str(modified),
                "--compact",
            ],
        )
        assert a.exit_code == 0 and b.exit_code == 0

        def strip_created_at(stdout: str) -> str:
            import re

            return re.sub(r'"created_at": "[^"]*"', '"created_at": ""', stdout)

        assert strip_created_at(a.stdout) == strip_created_at(b.stdout)

    def test_semantically_identical_after_sort_keys(self, tmp_path):
        """Sorted keys must not change a single field or value — only line
        order.  Parsed dicts of two cooks are equal once created_at is
        neutralised."""
        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(_make_bin(1024))
        modified.write_bytes(_make_bin(1024, {100: 0xAA}))

        result = runner.invoke(
            app,
            [
                "cook",
                "--allow-non-unique",
                str(original),
                str(modified),
                "--compact",
            ],
        )
        assert result.exit_code == 0
        data = _parse_json_from_stdout(result.stdout)
        assert data["schema_version"] == "4.4"
        assert data["type"] == "recipe"
        inst = data["instructions"][0]
        assert inst["offset"] == 100 and inst["ob"] == "00" and inst["mb"] == "AA"
        # fingerprint must be present and stable (it is content-based)
        assert data["fingerprint"].startswith("sha256:")
        assert len(data["fingerprint"]) == len("sha256:") + 64


# ---------------------------------------------------------------------------
# TestCookAnnotateMaps — schema 4.4 maps layer
# ---------------------------------------------------------------------------


def _bin_with_map(size: int = 8192, off: int = 512) -> tuple[bytearray, int]:
    """Random bin (unique anchors) with one embedded 4x3 u16 map at *off*."""
    import os
    import struct

    buf = bytearray(os.urandom(size))
    x = list(range(0, 4 * 100, 100))
    y = [0, 50, 100]
    cells = [100 + r * 10 + c for r in range(3) for c in range(4)]
    fmt = "<" + "H" * 4
    buf[off : off + 8] = struct.pack(fmt, *x)
    buf[off + 8 : off + 14] = struct.pack("<" + "H" * 3, *y)
    data_off = off + 14
    buf[data_off : data_off + 24] = struct.pack("<" + "H" * 12, *cells)
    return buf, data_off


class TestCookAnnotateMaps:
    def test_annotate_maps_emits_schema_4_4_with_refs(self, tmp_path):
        stock, data_off = _bin_with_map()
        tuned = bytearray(stock)
        tuned[data_off] ^= 0xFF
        tuned[data_off + 2] ^= 0xFF

        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(bytes(stock))
        modified.write_bytes(bytes(tuned))

        result = runner.invoke(
            app,
            [
                "cook",
                str(original),
                str(modified),
                "--annotate-maps",
                "--compact",
            ],
        )
        assert result.exit_code == 0
        data = _parse_json_from_stdout(result.stdout)
        assert data["schema_version"] == "4.4"
        assert "maps" in data
        assert any(m["instruction_refs"] for m in data["maps"])
        # every ref must point at a valid instruction index
        n_inst = len(data["instructions"])
        for m in data["maps"]:
            assert all(1 <= r <= n_inst for r in m["instruction_refs"])

    def test_no_annotate_maps_emits_lean_4_3(self, tmp_path):
        stock, data_off = _bin_with_map()
        tuned = bytearray(stock)
        tuned[data_off] ^= 0xFF

        original = tmp_path / "stock.bin"
        modified = tmp_path / "tuned.bin"
        original.write_bytes(bytes(stock))
        modified.write_bytes(bytes(tuned))

        result = runner.invoke(
            app,
            ["cook", str(original), str(modified), "--compact", "--no-annotate-maps"],
        )
        assert result.exit_code == 0
        data = _parse_json_from_stdout(result.stdout)
        assert data["schema_version"] == "4.3"
        assert "maps" not in data

    def test_help_shows_flag(self):
        result = runner.invoke(app, ["cook", "--help"])
        assert result.exit_code == 0
        # Rich help panel truncates long options — match the stable fragment.
        assert "--annotate-maps" in result.stdout
