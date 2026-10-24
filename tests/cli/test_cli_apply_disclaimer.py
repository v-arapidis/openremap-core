"""
The apply commands' checksum disclaimer.

``APPLIED`` is a statement about the recipe. It is **not** a statement about the
file: this engine writes the calibrated bytes and nothing else, so wherever the
family carries a checksum the stored value is now stale and the ECU can reject
the flash. Four commands share one printer (``tune-strict`` owns it), so the
disclaimer is asserted once, per verdict, at that seam.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openremap.cli.commands.tune_strict import _print_checksums, _print_disclaimer
from openremap.cli.main import app

runner = CliRunner()


def _capture(checks: dict, output: Path | None = None, capsys=None) -> str:
    _print_checksums(checks, output)
    return capsys.readouterr().out


@pytest.mark.parametrize(
    "status,phrase",
    [
        ("ok", "every scheme we know still verifies"),
        ("fail", "STALE"),
        ("skip", "no known scheme for this family"),
    ],
)
def test_every_verdict_says_not_corrected(status, phrase, capsys):
    text = _capture(
        {"status": status, "message": "ME7 main", "check_with": "openremap checksum"},
        Path("stock_tuned.bin"),
        capsys,
    )
    assert "NOT CORRECTED" in text
    assert phrase in text
    assert "openremap checksum stock_tuned.bin" in text


def test_a_stale_scheme_names_what_is_stale(capsys):
    text = _capture(
        {"status": "fail", "message": "ME7 main; MS43 CRC16"}, None, capsys
    )
    assert "ME7 main; MS43 CRC16" in text
    # no output path yet: the pointer must still be runnable-looking, not blank
    assert "openremap checksum <written file>" in text


def test_a_report_without_a_verdict_prints_nothing(capsys):
    """A refusal writes no file, so there is nothing to warn about."""
    assert _capture({}, None, capsys) == ""


def test_the_commands_are_registered():
    """The disclaimer points at a command the build must actually have."""
    result = runner.invoke(app, ["checksum", "--help"])
    assert result.exit_code == 0, result.output


def test_the_disclaimer_is_printed_and_fits_the_terminal(capsys):
    from openremap.core.services.recipes.apply.report import DISCLAIMER

    _print_disclaimer(DISCLAIMER)
    out = capsys.readouterr().out.splitlines()
    text = " ".join(line.strip() for line in out)
    assert "does not correct checksums automatically" in text
    assert "dedicated tuner shop" in text
    assert "right to repair and track racing" in text
    # the third sentence used to say "on the road or off it"; it now draws the
    # boundary at right-to-repair and track use instead.
    assert "everyday car" in text
    assert out[0].startswith("  disclaimer")
    for line in out:
        assert len(line) <= 80, line


def test_no_disclaimer_prints_nothing(capsys):
    _print_disclaimer(None)
    assert capsys.readouterr().out == ""
