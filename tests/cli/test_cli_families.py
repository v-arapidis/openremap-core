"""Tests for ``openremap families`` — including the rapidfuzz fuzzy fallback."""

from __future__ import annotations

from typer.testing import CliRunner

from openremap.cli.main import app

runner = CliRunner()


class TestFamiliesFuzzy:
    def test_exact_name_still_works(self):
        result = runner.invoke(app, ["families", "--family", "EDC16"])
        assert result.exit_code == 0
        assert "EDC16" in result.stdout
        assert "Error: unknown family" not in result.stdout

    def test_exact_alias_still_works(self):
        result = runner.invoke(app, ["families", "--family", "me7"])
        assert result.exit_code == 0

    def test_typo_suggests_closest_and_exits_one(self):
        """A near-miss family name prints suggestions and still exits 1."""
        result = runner.invoke(app, ["families", "--family", "edc16c"])
        assert result.exit_code == 1
        assert "Error: unknown family" in result.stderr
        assert "Closest families:" in result.stderr
        assert "EDC16" in result.stderr  # a sensible suggestion is present

    def test_garbage_query_no_suggestions(self):
        """A totally unrelated query gets no fuzzy suggestions (score cutoff)."""
        result = runner.invoke(app, ["families", "--family", "qqqqzzzz"])
        assert result.exit_code == 1
        assert "Error: unknown family" in result.stderr
        assert "Closest families:" not in result.stderr

    def test_no_family_flag_lists_table(self):
        result = runner.invoke(app, ["families"])
        assert result.exit_code == 0
        assert "FAMILY" in result.stdout
