"""
Health report tests (`openremap/core/services/health.py`).

Synthetic: each check's failure/warning mode (stale checksum, too-few
maps, embedded erased block, VIN duplication).  Corpus: known-good
factory files report healthy (warns allowed, no fails).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openremap.core.services.health import health_report

DATA = Path(__file__).resolve().parents[3] / "tests" / "data"
HAS_ME71 = (DATA / "ECUs" / "Bosch" / "ME7.1").is_dir()


def _statuses(report):
    return {c.name: c.status for c in report.checks}


# ---------------------------------------------------------------------------
# Baseline — healthy factory files
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_ME71, reason="ME7.1 corpus missing")
class TestHealthyFactory:
    def test_me71_factory_is_healthy(self):
        data = (DATA / "ECUs" / "Bosch" / "ME7.1" / "8D0907551M-0001.bin").read_bytes()
        r = health_report(data, "8D0907551M-0001.bin")
        assert r.healthy
        assert r.family == "ME7.1"
        assert _statuses(r)["checksums"] == "ok"
        assert _statuses(r)["identity"] == "ok"

    def test_me71_flip_byte_fails_checksum(self):
        data = bytearray((DATA / "ECUs" / "Bosch" / "ME7.1" / "8D0907551M-0001.bin").read_bytes())
        data[0x40000] ^= 0xFF  # inside the checksummed calibration area
        r = health_report(bytes(data), "flipped.bin")
        assert _statuses(r)["checksums"] == "fail"
        assert not r.healthy


# ---------------------------------------------------------------------------
# Synthetic failure modes
# ---------------------------------------------------------------------------


class TestSyntheticFailures:
    def test_unknown_binary_warns_identity(self):
        # 128 KB random: no detector claims it, stays healthy (warn only)
        r = health_report(os.urandom(0x20000), "random.bin")
        assert _statuses(r)["identity"] == "warn"
        assert r.healthy  # unknown is warn, not fail

    def test_erased_blocks_embedded_warns(self):
        # 1 MB: 0x30000-0x3FFFF = 0xFF fill in the middle, data around it
        data = bytearray(os.urandom(0x100000))
        data[0x30000:0x40000] = b"\xff" * 0x10000
        r = health_report(bytes(data), "erased.bin")
        assert _statuses(r)["erased blocks"] == "warn"

    def test_vins_duplicate_warns(self):
        # ISO 3779 check-digit-valid VINs with a whitelisted WMI (WVW),
        # valid year char at position 10, standalone 17-char runs.
        def make_vin(serial: int) -> bytes:
            translit = {c: int(v) for c, v in zip(
                "ABCDEFGHJKLMNPRSTUVWXYZ",
                "123456781234578923456789")}
            weights = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
            base = "WVWZZZ1JA" + f"{serial:07d}"
            # full 17 chars with a placeholder check digit (weight 0)
            full = base[:8] + "0" + base[8:]
            total = sum(
                (int(ch) if ch.isdigit() else translit[ch]) * w
                for ch, w in zip(full, weights)
            )
            rem = total % 11
            cd = "X" if rem == 10 else str(rem)
            return (base[:8] + cd + base[8:]).encode()

        vin1 = make_vin(1)
        vin2 = make_vin(2)
        assert len(vin1) == 17 and len(vin2) == 17
        data = bytearray(b"\xff" * 0x10000)
        data[0x100 : 0x100 + 17] = vin1
        data[0x300 : 0x300 + 17] = vin2
        r = health_report(bytes(data), "vins.bin")
        assert _statuses(r)["VINs"] == "warn"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestHealthCli:
    @pytest.mark.skipif(not HAS_ME71, reason="ME7.1 corpus missing")
    def test_cli_json_gate(self):
        from typer.testing import CliRunner
        from openremap.cli.main import app

        f = DATA / "ECUs" / "Bosch" / "ME7.1" / "8D0907551M-0001.bin"
        result = CliRunner().invoke(app, ["health", str(f), "--json"])
        assert result.exit_code == 0  # healthy → exit 0
        import json

        payload = json.loads(result.stdout)
        assert payload["healthy"] is True

    @pytest.mark.skipif(not HAS_ME71, reason="ME7.1 corpus missing")
    def test_cli_flipped_file_exits_one(self, tmp_path):
        from typer.testing import CliRunner
        from openremap.cli.main import app

        data = bytearray((DATA / "ECUs" / "Bosch" / "ME7.1" / "8D0907551M-0001.bin").read_bytes())
        data[0x40000] ^= 0xFF
        f = tmp_path / "flipped.bin"
        f.write_bytes(bytes(data))
        result = CliRunner().invoke(app, ["health", str(f), "--json"])
        assert result.exit_code == 1
        import json

        payload = json.loads(result.stdout)
        assert payload["healthy"] is False

    def test_cli_missing_file_error(self):
        from typer.testing import CliRunner
        from openremap.cli.main import app

        result = CliRunner().invoke(app, ["health", "/nonexistent/x.bin"])
        assert result.exit_code == 2  # click validates the path argument
        assert "Error" in result.stderr
