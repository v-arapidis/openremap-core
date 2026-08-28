"""Unit tests for the volatile classifier — synthetic fixtures only.

No corpus files, no real binaries — everything runs in well under a
second.  Real-corpus validation lives in Phase 5 of notes/recipes/cook-volatile.md.
"""

from __future__ import annotations

import struct

from openremap.core.services.checksums.nefmoto import (
    MultiRangeChecksumInfo,
    RollingChecksumEntry,
)
from openremap.core.services.identify.vin_scanner import is_valid_check_digit
from openremap.core.services.recipes import volatile as volatile_module
from openremap.core.services.recipes.volatile import (
    KIND_CHECKSUM_STORE,
    KIND_COUNTER_OR_SERIAL,
    KIND_SERIAL_OR_IDENT,
    KIND_VIN,
    classify_volatile,
    collect_checksum_stores,
)
from tests.tuning.services.test_denso_checksum import build_denso
from tests.tuning.services.test_ms43 import build_ms43

# Real-shaped VIN: known VW WMI, position-9 check digit '3' verified
# against vin_scanner.is_valid_check_digit, 'X' model year, numeric tail.
VALID_VIN = "WVWZZZ1J3XW123456"


def _inst(offset: int, size: int, ob: str, mb: str, ctx_entropy: float = 6.0) -> dict:
    return {
        "offset": offset,
        "offset_hex": f"{offset:X}",
        "size": size,
        "ob": ob,
        "mb": mb,
        "ctx": "",
        "context_after": "",
        "ctx_entropy": ctx_entropy,
        "ctx_unique": True,
    }


def _recipe(*instructions: dict) -> dict:
    return {"schema_version": "4.4", "instructions": list(instructions)}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _vin_stock() -> bytes:
    """Binary with a mirrored VIN inside a printable-ASCII ident block.

    Lowercase padding: printable ASCII (satisfies find_ident_blocks) but
    outside the [A-Z0-9]{17} regex class, so the non-overlapping
    finditer windows align exactly on the VIN.
    """
    pad = b"x" * 40
    run = pad + VALID_VIN.encode("ascii") + pad
    data = bytearray(b"\x00" * 2048)
    data[0x100 : 0x100 + len(run)] = run
    data[0x200 : 0x200 + len(VALID_VIN)] = VALID_VIN.encode("ascii")  # mirror
    return bytes(data)


def _me7_stock() -> bytes:
    """Binary with a ME7 main descriptor + (v, ~v) pair at end-0x20.

    Descriptor words per detect_me7: [0x800000, 0x80FBFF, 0x820000,
    0x87FFFF] (block1 end 0x87FFFF or 0x8FFFFF both accepted).
    """
    data = bytearray(b"\x00" * 4096)
    descriptor = struct.pack("<4I", 0x800000, 0x80FBFF, 0x820000, 0x87FFFF)
    data[0x100:0x110] = descriptor
    stored_offset = len(data) - 0x20
    struct.pack_into("<2I", data, stored_offset, 0x12345678, 0xEDCBA987)
    return bytes(data)


def _multipoint_stock() -> bytes:
    """Binary with a self-verifying ME7 multipoint descriptor at 0x200."""
    data = bytearray(b"\x00" * 4096)
    # Range data[0x300:0x304] is all zeros → LE u16 sum (u32) = 0.
    # 16-byte descriptor: (start, end, checksum, ~checksum).
    struct.pack_into("<4I", data, 0x200, 0x800300, 0x800304, 0, 0xFFFFFFFF)
    return bytes(data)


def _ident_stock() -> bytes:
    """Binary with one printable-ASCII ident run."""
    data = bytearray(b"\x00" * 2048)
    data[0x100 : 0x100 + 80] = b"A" * 80
    return bytes(data)


# ---------------------------------------------------------------------------
# Fixture sanity
# ---------------------------------------------------------------------------


def test_fixture_vin_is_valid():
    assert is_valid_check_digit(VALID_VIN)
    assert len(VALID_VIN) == 17


# ---------------------------------------------------------------------------
# collect_checksum_stores
# ---------------------------------------------------------------------------


def test_collect_stores_me7_main():
    stores = collect_checksum_stores(_me7_stock())
    stored = len(_me7_stock()) - 0x20
    assert stores == (("ME7 main", stored, stored + 8),)


def test_collect_stores_multipoint():
    stores = collect_checksum_stores(_multipoint_stock())
    assert stores == (("ME7 multipoint", 0x208, 0x210),)


def test_collect_stores_combined_deduplicated_and_sorted():
    data = bytearray(_me7_stock())
    # Range data[0x200:0x204] is zeros (sum 0) — must not overlap the
    # descriptor itself (0x300-0x310).
    struct.pack_into("<4I", data, 0x300, 0x800200, 0x800204, 0, 0xFFFFFFFF)
    stores = collect_checksum_stores(bytes(data))
    stored = len(data) - 0x20
    # Sorted by start offset: multipoint (0x308) precedes ME7 main (end-0x20).
    assert stores == (
        ("ME7 multipoint", 0x308, 0x310),
        ("ME7 main", stored, stored + 8),
    )


def test_collect_stores_empty_on_plain_binary():
    assert collect_checksum_stores(b"\x00" * 4096) == ()


def test_collect_stores_ironfelix_gs20():
    # 64 KB file → detect_gs20 fires on size alone (no signature gate);
    # store = LE16 CRC-16/ARC @ 0x0D.
    stores = collect_checksum_stores(b"\x00" * 65536)
    assert ("IronFelix gs20/crc16_arc", 0x0D, 0x0F) in stores


def test_ironfelix_store_instruction_excluded():
    stock = b"\x00" * 65536
    inst = _inst(0x0D, 2, "0000", "FFFF")
    report = classify_volatile(_recipe(inst), stock)

    assert len(report.excluded) == 1
    assert report.excluded[0].kind == KIND_CHECKSUM_STORE
    assert "IronFelix gs20" in report.excluded[0].evidence[0]


# ---------------------------------------------------------------------------
# classify_volatile — exclusion classes
# ---------------------------------------------------------------------------


def test_vin_instruction_excluded():
    stock = _vin_stock()
    vin_offset = 0x100 + 40  # the VIN sits after the A-padding
    inst = _inst(vin_offset + 2, 4, VALID_VIN[2:6].encode().hex().upper(), "55555555")
    report = classify_volatile(_recipe(inst), stock)

    assert report.flagged == []
    assert len(report.excluded) == 1
    finding = report.excluded[0]
    assert finding.kind == KIND_VIN
    assert finding.action == "excluded"
    assert finding.index == 0
    assert finding.confidence >= 0.9
    assert VALID_VIN in finding.evidence[0]


def test_checksum_store_instruction_excluded():
    stock = _me7_stock()
    stored = len(stock) - 0x20
    inst = _inst(stored + 2, 4, "12345678", "ABCDEF01")
    report = classify_volatile(_recipe(inst), stock)

    assert report.flagged == []
    assert len(report.excluded) == 1
    finding = report.excluded[0]
    assert finding.kind == KIND_CHECKSUM_STORE
    assert finding.action == "excluded"
    assert finding.confidence == 0.95
    assert "ME7 main" in finding.evidence[0]


def test_multipoint_store_instruction_excluded():
    stock = _multipoint_stock()
    inst = _inst(0x208, 4, "00000000", "FFFFFFFF")
    report = classify_volatile(_recipe(inst), stock)

    assert len(report.excluded) == 1
    assert report.excluded[0].kind == KIND_CHECKSUM_STORE
    assert "ME7 multipoint" in report.excluded[0].evidence[0]


def test_clean_instruction_kept():
    stock = _me7_stock()
    inst = _inst(0x50, 4, "00000000", "DEADBEEF")
    report = classify_volatile(_recipe(inst), stock)
    assert report.excluded == []
    assert report.flagged == []


# ---------------------------------------------------------------------------
# classify_volatile — flagged classes
# ---------------------------------------------------------------------------


def test_ident_block_ascii_change_flagged():
    stock = _ident_stock()
    inst = _inst(0x110, 4, "AAAAAAAA", "5758595A")  # "WXYZ"
    report = classify_volatile(_recipe(inst), stock)

    assert report.excluded == []
    assert len(report.flagged) == 1
    finding = report.flagged[0]
    assert finding.kind == KIND_SERIAL_OR_IDENT
    assert finding.action == "flagged"
    assert finding.confidence == 0.5


def test_low_entropy_instruction_flagged():
    stock = b"\x00" * 2048
    inst = _inst(0x50, 4, "00000000", "11223344", ctx_entropy=1.0)
    report = classify_volatile(_recipe(inst), stock)

    assert report.excluded == []
    assert len(report.flagged) == 1
    finding = report.flagged[0]
    assert finding.kind == KIND_COUNTER_OR_SERIAL
    assert finding.action == "flagged"
    assert finding.confidence == 0.3


def test_exclude_uncertain_promotes_flags():
    stock = b"\x00" * 2048
    inst = _inst(0x50, 4, "00000000", "11223344", ctx_entropy=1.0)
    report = classify_volatile(_recipe(inst), stock, exclude_uncertain=True)

    assert report.flagged == []
    assert len(report.excluded) == 1
    assert report.excluded[0].kind == KIND_COUNTER_OR_SERIAL
    assert report.excluded[0].action == "excluded"


# ---------------------------------------------------------------------------
# Precedence, report shape, determinism
# ---------------------------------------------------------------------------


def test_excluded_finding_suppresses_weak_flags():
    # A VIN instruction sits inside the ident block with ASCII bytes —
    # it must report ONLY the VIN exclusion, not SERIAL_OR_IDENT.
    stock = _vin_stock()
    vin_offset = 0x100 + 40
    inst = _inst(vin_offset, 4, "5756575A", "55555555", ctx_entropy=1.0)
    report = classify_volatile(_recipe(inst), stock)

    assert len(report.excluded) == 1
    assert report.excluded[0].kind == KIND_VIN
    assert report.flagged == []


def test_empty_recipe():
    report = classify_volatile(_recipe(), _me7_stock())
    assert report.excluded == []
    assert report.flagged == []
    assert report.to_dict()["summary"] == {
        "excluded_count": 0,
        "flagged_count": 0,
        "bytes_excluded": 0,
    }


def test_determinism():
    stock = _me7_stock()
    stored = len(stock) - 0x20
    recipe = _recipe(
        _inst(stored, 4, "12345678", "ABCDEF01"),
        _inst(0x50, 4, "00000000", "11223344", ctx_entropy=1.0),
        _inst(0x60, 4, "00000000", "DEADBEEF"),
    )
    a = classify_volatile(recipe, stock).to_dict()
    b = classify_volatile(recipe, stock).to_dict()
    assert a == b


def test_report_to_dict_shape():
    stock = _me7_stock()
    stored = len(stock) - 0x20
    inst = _inst(stored + 1, 2, "3456", "FFFF")
    d = classify_volatile(_recipe(inst), stock).to_dict()

    assert set(d) == {"excluded", "flagged", "summary"}
    assert d["summary"] == {
        "excluded_count": 1,
        "flagged_count": 0,
        "bytes_excluded": 2,
    }
    entry = d["excluded"][0]
    assert set(entry) == {
        "index",
        "offset",
        "offset_hex",
        "size",
        "kind",
        "confidence",
        "action",
        "evidence",
    }
    assert entry["kind"] == KIND_CHECKSUM_STORE


# ---------------------------------------------------------------------------
# Integration — MS43 / Denso / NefMoto store collection (Phase 1 close-out)
# ---------------------------------------------------------------------------


def test_missing_size_fallback_derives_from_ob():
    """A recipe instruction without `size` must still get its true range
    from the ob hex length — the fallback must NOT halve it."""
    stock = _me7_stock()
    stored = len(stock) - 0x20
    # Range [stored-2, stored+2) overlaps the [stored, stored+8) store;
    # with the halving bug the range was [stored-2, stored) → missed.
    inst = _inst(stored - 2, 4, "12345678", "FFFFFFFF")
    del inst["size"]
    report = classify_volatile(_recipe(inst), stock)

    assert len(report.excluded) == 1
    assert report.excluded[0].kind == KIND_CHECKSUM_STORE
    assert report.excluded[0].size == 4


def test_store_adjacent_instructions_kept():
    """Instructions immediately before/after a store (no overlap) are
    calibration instructions, not volatile stores."""
    stock = _me7_stock()
    stored = len(stock) - 0x20
    before = _inst(stored - 4, 4, "00000000", "DEADBEEF")  # ends at store start
    after = _inst(stored + 8, 4, "00000000", "DEADBEEF")   # starts at store end
    report = classify_volatile(_recipe(before, after), stock)

    assert report.excluded == []
    assert report.flagged == []


def test_collect_stores_ms43_three_slots():
    """The MS43 profile's three CRC16 slots (boot/program/calibration)
    are all reported as stores — 2 bytes each at the slot itself."""
    from openremap.core.services.checksums.ms43 import (
        _BOOT_SLOT,
        _CAL_SLOT,
        _PROG_SLOT,
    )

    stores = collect_checksum_stores(build_ms43())
    for slot in (_BOOT_SLOT, _PROG_SLOT, _CAL_SLOT):
        assert ("MS43", slot, slot + 2) in stores


def test_ms43_store_instruction_excluded():
    from openremap.core.services.checksums.ms43 import _BOOT_SLOT

    stock = build_ms43()
    inst = _inst(_BOOT_SLOT, 2, "0000", "FFFF")
    report = classify_volatile(_recipe(inst), stock)

    assert any(
        f.kind == KIND_CHECKSUM_STORE and "MS43" in f.evidence[0]
        for f in report.excluded
    )


def test_collect_stores_denso_diff_fields():
    """Each verifying Denso descriptor entry reports its 4-byte diff
    field (entry offset + 8) as a store."""
    from openremap.core.services.checksums.denso import CHECK_TOTAL
    from tests.tuning.services.test_denso_checksum import _TABLE, _ENTRIES

    stores = collect_checksum_stores(build_denso())
    denso_stores = [s for s in stores if s[0] == "Denso"]
    # 12 verifying entries; the 5 trailing disabled slots are skipped.
    assert len(denso_stores) == _ENTRIES
    for k in range(_ENTRIES):
        off = _TABLE + k * 12 + 8
        assert ("Denso", off, off + 4) in stores


def test_denso_disabled_entries_are_not_stores():
    """Disabled descriptor entries ([0,0,0], no stored diff) must not be
    reported as stores — their slots are constant, not volatile."""
    from tests.tuning.services.test_denso_checksum import _ENTRIES, _TABLE

    stores = collect_checksum_stores(build_denso())
    for k in range(_ENTRIES, _ENTRIES + 5):  # trailing disabled entries
        off = _TABLE + k * 12 + 8
        assert all(
            not (s[0] == "Denso" and s[1] == off) for s in stores
        )


def test_denso_store_instruction_excluded():
    from tests.tuning.services.test_denso_checksum import _TABLE

    stock = build_denso()
    inst = _inst(_TABLE + 8, 4, "00000000", "FFFFFFFF")
    report = classify_volatile(_recipe(inst), stock)

    assert any(
        f.kind == KIND_CHECKSUM_STORE and "Denso" in f.evidence[0]
        for f in report.excluded
    )


def test_collect_stores_rolling_and_multirange(monkeypatch):
    """NefMoto rolling slots (4 bytes) and the multirange (v,~v) store
    (8 bytes) map to the expected ranges.  Real dataclass shapes — the
    detector itself only fires on real C166 firmware (corpus, Phase 5),
    so the mapping is exercised with genuine entry objects."""
    monkeypatch.setattr(
        volatile_module,
        "detect_me7_rolling",
        lambda data: [
            RollingChecksumEntry(
                store_offset=0x100, ranges=(), init_range=None,
                stored=0, expected=0, status="ok",
            )
        ],
    )
    monkeypatch.setattr(
        volatile_module,
        "detect_me7_multirange",
        lambda data: MultiRangeChecksumInfo(
            store_offset=0x200, ranges=(), stored=0, inv_stored=0,
            expected=0, status="ok",
        ),
    )

    stores = collect_checksum_stores(b"\x00" * 0x10000)
    assert ("NefMoto rolling", 0x100, 0x104) in stores
    assert ("NefMoto multirange", 0x200, 0x208) in stores


def test_rolling_store_instruction_excluded(monkeypatch):
    monkeypatch.setattr(
        volatile_module,
        "detect_me7_rolling",
        lambda data: [
            RollingChecksumEntry(
                store_offset=0x100, ranges=(), init_range=None,
                stored=0, expected=0, status="ok",
            )
        ],
    )

    inst = _inst(0x100, 4, "00000000", "FFFFFFFF")
    report = classify_volatile(_recipe(inst), b"\x00" * 0x10000)

    assert any(
        f.kind == KIND_CHECKSUM_STORE and "NefMoto rolling" in f.evidence[0]
        for f in report.excluded
    )
