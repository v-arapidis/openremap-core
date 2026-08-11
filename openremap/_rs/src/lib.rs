//! Native acceleration for openremap-core CPU-bound algorithms.
//!
//! This crate is compiled to a native shared library via PyO3 + maturin
//! and loaded as `openremap._rust`.  Every public function here has a
//! pure-Python equivalent in `openremap.core.services.*` that serves as
//! both the specification and the fallback when the native library is
//! unavailable (PyPy, unsupported platform, source install without Rust).
//!
//! # Correctness guarantee
//!
//! Each function MUST produce bit-identical output to its Python counterpart
//! for all inputs.  The test suite runs an oracle fuzz harness that feeds
//! random data to both backends and asserts equality.  Any divergence is a
//! bug in the Rust port — the Python implementation is the specification.

use pyo3::prelude::*;

mod diff;
mod entropy;
mod map_hunter;

#[pymodule]
fn _rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // ── Entropy ──────────────────────────────────────────────────────────
    m.add_function(wrap_pyfunction!(entropy::shannon_entropy, m)?)?;
    m.add_function(wrap_pyfunction!(entropy::is_low_entropy, m)?)?;

    // ── Diff ─────────────────────────────────────────────────────────────
    m.add_function(wrap_pyfunction!(diff::find_changed_blocks, m)?)?;

    // ── Map hunter ───────────────────────────────────────────────────────
    m.add_function(wrap_pyfunction!(map_hunter::scan_map_axes, m)?)?;
    m.add_function(wrap_pyfunction!(map_hunter::scan_map_tables, m)?)?;
    Ok(())
}
