//! Native acceleration for openremap-core CPU-bound algorithms.
//!
//! This crate is compiled to a native shared library via PyO3 + maturin
//! and loaded as `openremap._rust`.  It is **mandatory** — there is no
//! pure-Python fallback (see CLAUDE.md / AGENTS.md).  Behavioural
//! correctness is covered by the test suite (e.g.
//! `tests/tuning/services/test_map_hunter.py`, `tests/tuning/test_entropy.py`).

use pyo3::prelude::*;

mod arch;
mod checksums;
mod identify;
mod maps;
mod primitives;
mod recipes;

#[pymodule]
fn _rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // ── Endianness ───────────────────────────────────────────────────────
    m.add_function(wrap_pyfunction!(identify::endian::detect_endian, m)?)?;

    // ── C166 / ST10 ──────────────────────────────────────────────────────
    m.add_function(wrap_pyfunction!(arch::c166::c166_references, m)?)?;
    m.add_function(wrap_pyfunction!(arch::c166::c166_walk, m)?)?;

    // ── Layout ───────────────────────────────────────────────────────────
    m.add_function(wrap_pyfunction!(maps::layout_scan::find_ident_blocks, m)?)?;

    // ── CRC-16/ARC ───────────────────────────────────────────────────────
    m.add_function(wrap_pyfunction!(primitives::crc16::crc16_arc, m)?)?;

    // ── Denso Subaru ─────────────────────────────────────────────────────
    m.add_function(wrap_pyfunction!(checksums::denso::detect_denso, m)?)?;

    // ── NefMoto ME7 ──────────────────────────────────────────────────────
    m.add_function(wrap_pyfunction!(checksums::nefmoto_scan::locate_pattern, m)?)?;
    m.add_function(wrap_pyfunction!(checksums::nefmoto_scan::rolling_checksum, m)?)?;

    // ── Entropy ──────────────────────────────────────────────────────────
    m.add_function(wrap_pyfunction!(primitives::entropy::shannon_entropy, m)?)?;
    m.add_function(wrap_pyfunction!(primitives::entropy::is_low_entropy, m)?)?;

    // ── Diff ─────────────────────────────────────────────────────────────
    m.add_function(wrap_pyfunction!(recipes::diff::find_changed_blocks, m)?)?;

    // ── Map hunter ───────────────────────────────────────────────────────
    m.add_function(wrap_pyfunction!(maps::map_hunter::scan_map_axes, m)?)?;
    m.add_function(wrap_pyfunction!(maps::map_hunter::scan_map_tables, m)?)?;

    // ── Checksum sweep ───────────────────────────────────────────────────
    m.add_function(wrap_pyfunction!(checksums::checksum::checksum_compute, m)?)?;
    m.add_function(wrap_pyfunction!(checksums::checksum::me7_multipoint_scan, m)?)?;
    Ok(())
}
