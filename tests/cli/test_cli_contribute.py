"""
Tests for ``openremap contribute``.

The CLI is presentation plus file writing, so that is what is pinned here: the
report reaches the terminal, ``--write`` puts the four artifacts where the
repository expects them, the dump itself is never copied anywhere, and a failed
verification exits non-zero (a compiled CLI is the only part of the wizard a CI
job can gate on).

``--write`` paths are repository-relative, so every test here runs inside
``tmp_path``: the wizard must never be able to touch the real tree by accident.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openremap.core.cli.main import app

runner = CliRunner()

MARKER = b"45/1/SID206/9/5033/DAMOS161/8D0907551"
SIZE = 0x40000


def write_dump(path: Path, *, marker: bytes = MARKER, sw: str = "1037359650") -> Path:
    buf = bytearray(b"\xff" * SIZE)
    buf[0x100 : 0x100 + len(marker)] = marker
    ident = f"0261204270 {sw}".encode()
    buf[0x400 : 0x400 + len(ident)] = ident
    path.write_bytes(bytes(buf))
    return path


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A throwaway cwd: every relative artifact path lands under it."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def no_checkout(monkeypatch):
    """Pretend to be a plain `pip install`: no recipes anywhere."""
    import openremap.core.services.wizard.fixtures as fixtures

    monkeypatch.setattr(fixtures, "find_recipes_dir", lambda start=None: None)
    fixtures.reset_cache()
    yield
    fixtures.reset_cache()


class TestContributeExtractor:
    def test_reports_without_writing_anything(self, workspace):
        dump = write_dump(workspace / "stock.bin")
        result = runner.invoke(
            app,
            [
                "contribute",
                "extractor",
                str(dump),
                "--brand",
                "Siemens",
                "--family",
                "SID206",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "Siemens SID206" in result.output
        assert "no collision" in result.output
        assert "steals no other family" in result.output
        # reading only: no artifacts, and no copy of the dump
        assert not (workspace / "_dev").exists()
        assert sorted(p.name for p in workspace.iterdir()) == ["stock.bin"]

    def test_json_mode_is_machine_readable(self, workspace):
        dump = write_dump(workspace / "stock.bin")
        result = runner.invoke(
            app,
            [
                "contribute",
                "extractor",
                str(dump),
                "--brand",
                "Siemens",
                "--family",
                "SID206",
                "--json",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["spec"]["marker"] == MARKER.decode("latin-1")

    def test_write_creates_the_four_artifacts(self, workspace):
        dump = write_dump(workspace / "stock.bin")
        result = runner.invoke(
            app,
            [
                "contribute",
                "extractor",
                str(dump),
                "--brand",
                "Siemens",
                "--family",
                "SID206",
                "--write",
                "--out",
                str(workspace),
            ],
        )

        assert result.exit_code == 0, result.output
        recipe = workspace / "_dev/contrib/recipes/siemens-sid206.json"
        extractor = workspace / "openremap/core/manufacturers/siemens/sid206/extractor.py"
        test = workspace / "tests/tuning/manufacturers/test_sid206_extractor.py"
        report = workspace / "sid206-contribution.md"

        assert all(p.is_file() for p in (recipe, extractor, test, report))
        assert (extractor.parent / "__init__.py").is_file()

        recipe_payload = json.loads(recipe.read_text())
        assert recipe_payload["expect"]["ecu_family"] == "SID206"
        assert recipe_payload["file_size"] == SIZE
        # The fixture carries the marker, never the contributor's bytes.
        assert recipe_payload["plants"][0]["seq"][0]["hex"] == MARKER.hex()
        assert MARKER in extractor.read_bytes()
        assert "Not verified" in report.read_text()

    def test_write_never_copies_the_dump_into_the_tree(self, workspace):
        dump = write_dump(workspace / "stock.bin")
        runner.invoke(
            app,
            [
                "contribute",
                "extractor",
                str(dump),
                "--brand",
                "Siemens",
                "--family",
                "SID206",
                "--write",
                "--out",
                str(workspace),
            ],
        )

        dump_bytes = dump.read_bytes()
        copied = [
            path
            for path in workspace.rglob("*")
            if path.is_file() and path.name != "stock.bin" and path.read_bytes() == dump_bytes
        ]
        assert copied == [], f"the dump was copied into the tree: {copied}"

    def test_a_colliding_marker_is_refused_not_shipped(self, workspace):
        """The honest failure: a marker inside another family's own token.

        `EDC16C34` is Bosch EDC16's ident token *and* a run the contributor's
        dump contains, so the only guard that would protect EDC16 is the very
        string this family is being identified by — the guard would reject the
        contributor's own files too. The wizard must refuse rather than emit a
        skeleton that steals EDC16 or one that never matches anything.
        """
        dump = write_dump(workspace / "stock.bin", marker=b"EDC16C34")
        result = runner.invoke(
            app,
            [
                "contribute",
                "extractor",
                str(dump),
                "--brand",
                "Bosch",
                "--family",
                "M9.9",
                "--marker",
                "EDC16C34",
                "--json",
            ],
        )
        payload = json.loads(result.output)
        assert payload["verification"]["ok"] is False
        assert result.exit_code == 1
        failed = {c["name"]: c["detail"] for c in payload["verification"]["checks"] if not c["ok"]}
        assert "claims its own fixture" in failed

    def test_bad_family_name_exits_non_zero(self, workspace):
        dump = write_dump(workspace / "stock.bin")
        result = runner.invoke(
            app,
            [
                "contribute",
                "extractor",
                str(dump),
                "--brand",
                "Siemens",
                "--family",
                "../escape",
            ],
        )
        assert result.exit_code == 1
        assert "invalid family" in result.output

    def test_strict_mode_without_a_checkout_says_what_to_do(self, workspace, no_checkout):
        """The default must not degrade silently — it must refuse and instruct."""
        dump = write_dump(workspace / "stock.bin")
        result = runner.invoke(
            app,
            ["contribute", "extractor", str(dump), "--brand", "Siemens", "--family", "SID206"],
        )
        assert result.exit_code == 1
        assert "no fixture recipes found" in result.output
        assert "git clone" in result.output
        assert "--measure-only" in result.output
        # and it must not tell the user to run the command that just failed
        assert "without --write" not in result.output

    def test_measure_only_writes_nothing_and_says_so(self, workspace, no_checkout):
        dump = write_dump(workspace / "stock.bin")
        result = runner.invoke(
            app,
            [
                "contribute",
                "extractor",
                str(dump),
                "--brand",
                "Siemens",
                "--family",
                "SID206",
                "--measure-only",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "MEASUREMENT ONLY" in result.output
        assert "no collision check" in result.output
        assert "unranked" in result.output
        assert sorted(p.name for p in workspace.iterdir()) == ["stock.bin"]

    def test_measure_only_shows_what_the_engine_thinks_the_dump_is(
        self, workspace, no_checkout
    ):
        """`identify: Unknown` is the line that answers "do I need to contribute?"."""
        dump = write_dump(workspace / "stock.bin")
        result = runner.invoke(
            app,
            [
                "contribute",
                "extractor",
                str(dump),
                "--brand",
                "Siemens",
                "--family",
                "SID206",
                "--measure-only",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "identify: Unknown" in result.output

    def test_measure_only_reports_a_known_family(self, workspace, no_checkout):
        """When an extractor does claim it, say which — that changes the advice.

        Built from the extractor's own documented magic rather than from a
        fixture: this test runs in the no-checkout mode, where no fixtures exist.
        """
        # Bosch DME-3.2: 0x22 + FF×4 + 0x02 header, <= 32KB (motronic_legacy).
        known = bytearray(b"\xff" * 0x8000)
        known[0:6] = bytes.fromhex("22ffffffff02")
        dump = workspace / "known.bin"
        dump.write_bytes(bytes(known))
        result = runner.invoke(
            app,
            [
                "contribute",
                "extractor",
                str(dump),
                "--brand",
                "Siemens",
                "--family",
                "SID206",
                "--measure-only",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "identify: Bosch DME-3.2" in result.output
        assert "already claims this dump" in result.output

    def test_measure_only_needs_no_family_name(self, workspace, no_checkout):
        """The first command a stranger runs must not ask for the answer."""
        dump = write_dump(workspace / "stock.bin")
        result = runner.invoke(
            app,
            ["contribute", "extractor", str(dump), "--measure-only"],
        )
        assert result.exit_code == 0, result.output
        assert "identify: Unknown" in result.output

    def test_strict_mode_needs_the_family_name_and_says_so(self, workspace):
        dump = write_dump(workspace / "stock.bin")
        result = runner.invoke(app, ["contribute", "extractor", str(dump)])
        assert result.exit_code == 1
        assert "--brand and --family are required" in result.output
        assert "--measure-only" in result.output

    def test_measure_only_json_is_one_document(self, workspace, no_checkout):
        dump = write_dump(workspace / "stock.bin")
        result = runner.invoke(
            app,
            [
                "contribute",
                "extractor",
                str(dump),
                "--brand",
                "Siemens",
                "--family",
                "SID206",
                "--measure-only",
                "--json",
            ],
        )
        payload = json.loads(result.output)
        assert payload["mode"] == "measure"
        assert payload["analysis"]["recommended"] is None

    def test_missing_dump_exits_non_zero(self, workspace):
        result = runner.invoke(
            app,
            [
                "contribute",
                "extractor",
                str(workspace / "nope.bin"),
                "--brand",
                "Siemens",
                "--family",
                "SID206",
            ],
        )
        assert result.exit_code == 1


class TestContributeCheck:
    def test_passes_on_a_shipped_extractor(self):
        result = runner.invoke(
            app,
            [
                "contribute",
                "check",
                "openremap/core/manufacturers/siemens/sid803/extractor.py",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "steals no other family" in result.output

    def test_flags_an_unregistered_extractor(self, workspace):
        """A generated extractor is not reachable until a human registers it."""
        dump = write_dump(workspace / "stock.bin")
        runner.invoke(
            app,
            [
                "contribute",
                "extractor",
                str(dump),
                "--brand",
                "Siemens",
                "--family",
                "SID206",
                "--write",
                "--out",
                str(workspace),
            ],
        )
        result = runner.invoke(
            app,
            [
                "contribute",
                "check",
                "openremap/core/manufacturers/siemens/sid206/extractor.py",
            ],
        )
        assert result.exit_code == 1
        assert "not imported by any brand package" in result.output

    def test_json_mode(self):
        result = runner.invoke(
            app,
            [
                "contribute",
                "check",
                "openremap/core/manufacturers/siemens/sid803/extractor.py",
                "--json",
            ],
        )
        payload = json.loads(result.output)
        assert payload["ok"] is True

    def test_missing_file_exits_non_zero(self, workspace):
        result = runner.invoke(app, ["contribute", "check", "nope.py"])
        assert result.exit_code == 1
