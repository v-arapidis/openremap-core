"""
ECU Identifier

Identifies a single ECU binary by iterating the manufacturer registry
and delegating to the first extractor that claims the binary.

Falls back to a generic response (unknown manufacturer) when no extractor
matches.

Returns the lean identity fields:
    manufacturer, match_key, ecu_family, ecu_variant,
    software_version, hardware_number, calibration_id,
    oem_part_number, detection_strength, detection_evidence,
    file_size, sha256 (full file).

Full rich-extraction logic is preserved cold in the legacy/ reference folder.
"""

import hashlib
from typing import Dict, Optional, Tuple

from openremap.core.manufacturers import get_extractors

# ── Endian detection ──────────────────────────────────────────────────────

# Runs on the compiled Rust backend (`_rs/src/endian.rs`) — a 1:1 port of
# the original Python heuristic, itself a port of the studio's
# `crates/studio/src/analysis/endian.rs`.  ~800x faster than the Python
# byte loop; parity verified against the Python output on 1,693 real
# corpus files + synthetic edge cases (2026-08-15, see
# docs/internal/audits/2026-08-15-rust-migration-audit.md).

# Maximum bytes to sample (same cap as the Rust backend).
_MAX_SAMPLE_BYTES = 256 * 1024

# Minimum non-trivial words required before committing (mirrors the Rust
# backend constant — the fallback decision happens natively).
_MIN_SAMPLES = 32

from openremap._rust import detect_endian as _rust_detect_endian  # type: ignore[import-untyped]


def _detect_endian(data: bytes, cell_bytes: int = 2) -> str:
    """
    Detect the byte order of *data* using the high-byte-zero heuristic.

    ECU calibration binaries are dominated by small unsigned integers
    (fuel-map cells, RPM breakpoints, lookup indices).  Under the correct
    byte order the high byte(s) of each word are almost always ``0x00``;
    under the wrong order those zeros migrate to the low byte and the
    high byte becomes random.

    The heuristic counts how often the high byte equals ``0x00`` for each
    candidate (little-endian vs big-endian).  The larger count wins.
    All-zero words are skipped — they look identical to both orders.

    Computed natively in Rust (`openremap._rust.detect_endian`); this
    wrapper keeps the historical module API stable.
    """
    return _rust_detect_endian(data, cell_bytes)


# ── Public API ────────────────────────────────────────────────────────────


def identify_ecu(data: bytes, filename: str = "unknown.bin") -> Dict:
    """
    Identify a single ECU binary.

    Iterates the manufacturer registry and delegates to the first extractor
    that can handle the binary. Falls back to a generic response when nothing
    matches.

    Args:
        data:     Raw bytes of the ECU binary.
        filename: Original filename — used for display only.

    Returns:
        Dict compatible with ECUIdentitySchema.
    """
    sha256 = hashlib.sha256(data).hexdigest()
    file_size = len(data)

    # Determine ECU hardware properties.
    # Cell width defaults to 2 (16-bit) — the same conservative default
    # the Rust editor uses.  Endianness is auto-detected from the binary.
    ecu_cell_bytes = 2
    ecu_endian = _detect_endian(data, ecu_cell_bytes)

    for extractor in get_extractors():
        if extractor.can_handle(data):
            rich = extractor.extract(data, filename)
            detection_strength = getattr(extractor, "detection_strength", None)
            detection_evidence = getattr(extractor, "last_detection_evidence", ())
            return _to_identity(
                rich,
                file_size,
                sha256,
                detection_strength,
                detection_evidence,
                ecu_endian,
                ecu_cell_bytes,
            )

    return _unknown_identity(file_size, sha256, ecu_endian, ecu_cell_bytes)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_identity(
    rich: Dict,
    file_size: int,
    sha256: str,
    detection_strength: Optional[str] = None,
    detection_evidence: Tuple[str, ...] = (),
    ecu_endian: str = "little",
    ecu_cell_bytes: int = 2,
) -> Dict:
    """
    Map the rich extractor output down to the lean identity fields.

    All other fields (md5, sha256_first_64kb, calibration_version,
    sw_base_version, serial_number, dataset_number, raw_strings)
    are intentionally dropped.

    ``oem_part_number`` is now included — Marelli and Delphi families
    use it as a primary identifier and the confidence scorer awards
    points for its presence.

    ``detection_strength`` is injected from the extractor class attribute
    rather than from the rich dict (it is a property of the extractor,
    not of a particular extraction result).

    ``detection_evidence`` is the tuple of evidence tags collected by the
    extractor's ``can_handle()`` method.  The confidence scorer uses the
    evidence count and composition to compute a dynamic detection-quality
    bonus that supersedes the static ``detection_strength`` value.

    ``ecu_endian`` and ``ecu_cell_bytes`` are hardware properties
    auto-detected from the binary via the high-byte-zero heuristic
    (see ``_detect_endian``).
    """
    return {
        "manufacturer": rich.get("manufacturer"),
        "match_key": rich.get("match_key"),
        "ecu_family": rich.get("ecu_family"),
        "ecu_variant": rich.get("ecu_variant"),
        "software_version": rich.get("software_version"),
        "hardware_number": rich.get("hardware_number"),
        "calibration_id": rich.get("calibration_id"),
        "oem_part_number": rich.get("oem_part_number"),
        "detection_strength": detection_strength,
        "detection_evidence": detection_evidence,
        "file_size": file_size,
        "sha256": sha256,
        "md5": rich.get("md5", ""),
        "calibration_version": rich.get("calibration_version"),
        "sw_base_version": rich.get("sw_base_version"),
        "serial_number": rich.get("serial_number"),
        "dataset_number": rich.get("dataset_number"),
        "raw_strings": list(rich.get("raw_strings") or []),
        "ident_block": rich.get("ident_block"),
        "ecu_endian": ecu_endian,
        "ecu_cell_bytes": ecu_cell_bytes,
    }


def _unknown_identity(
    file_size: int,
    sha256: str,
    ecu_endian: str = "little",
    ecu_cell_bytes: int = 2,
) -> Dict:
    """
    Fallback identity for unrecognised binaries.
    All identification fields are None; hardware properties are still
    detected from the binary.
    """
    return {
        "manufacturer": None,
        "match_key": None,
        "ecu_family": None,
        "ecu_variant": None,
        "software_version": None,
        "hardware_number": None,
        "calibration_id": None,
        "oem_part_number": None,
        "detection_strength": None,
        "detection_evidence": (),
        "file_size": file_size,
        "sha256": sha256,
        "md5": "",
        "calibration_version": None,
        "sw_base_version": None,
        "serial_number": None,
        "dataset_number": None,
        "raw_strings": [],
        "ident_block": None,
        "ecu_endian": ecu_endian,
        "ecu_cell_bytes": ecu_cell_bytes,
    }
