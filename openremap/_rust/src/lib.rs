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

mod entropy;

/// Accelerated Shannon entropy and context-anchor search.
///
/// Exposes the same public API as `openremap.core.services.entropy`:
///
///   - ``shannon_entropy(data: bytes) -> float``
///   - ``is_low_entropy(data: bytes, threshold: float = 2.5) -> bool``
///   - ``count_unique_in_window(haystack: bytes, needle: bytes, start: int, end: int) -> int``
///   - ``find_unique_context(data, offset, size, ob, min_size=32, max_size=512, threshold=2.5) -> tuple``
#[pymodule]
fn _rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(entropy::shannon_entropy, m)?)?;
    m.add_function(wrap_pyfunction!(entropy::is_low_entropy, m)?)?;
    m.add_function(wrap_pyfunction!(entropy::count_unique_in_window, m)?)?;
    m.add_function(wrap_pyfunction!(entropy::find_unique_context, m)?)?;
    Ok(())
}
