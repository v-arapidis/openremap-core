//! Shannon entropy for ECU binary analysis.
//!
//! Python handles context-anchor search (`count_unique_in_window` and
//! `find_unique_context`) — CPython's C-level `bytes.find` (Two-Way
//! / FASTSEARCH) is slightly faster for that workload.  Rust handles
//! the math-heavy `shannon_entropy` which is 36–75× faster than the
//! pure-Python `collections.Counter` loop.

use pyo3::prelude::*;

// ---------------------------------------------------------------------------
// shannon_entropy
// ---------------------------------------------------------------------------

/// Shannon entropy in bits per byte, rounded to 4 decimal places.
///
/// Returns 0.0 for empty data or perfectly uniform data.
/// Returns up to 8.0 for perfectly random data.
///
/// Uses a ``[u32; 256]`` frequency array — no heap allocations, no hashing.
#[pyfunction]
#[pyo3(signature = (data))]
pub fn shannon_entropy(data: &[u8]) -> f64 {
    if data.is_empty() {
        return 0.0;
    }

    let total = data.len() as f64;

    let mut counts = [0u32; 256];
    for &b in data {
        counts[b as usize] = counts[b as usize].saturating_add(1);
    }

    let mut entropy = 0.0;
    for &count in &counts {
        if count == 0 {
            continue;
        }
        let p = count as f64 / total;
        entropy -= p * p.ln();
    }

    let entropy_log2 = entropy / std::f64::consts::LN_2;
    (entropy_log2 * 10000.0).round() / 10000.0
}

// ---------------------------------------------------------------------------
// is_low_entropy
// ---------------------------------------------------------------------------

/// Return `True` when the Shannon entropy of `data` is below `threshold`.
///
/// The default threshold of 2.5 bits/byte is the same floor used by the
/// recipe builder to distinguish structured calibration data from padding
/// or repetitive fill patterns.
#[pyfunction]
#[pyo3(signature = (data, threshold = 2.5))]
pub fn is_low_entropy(data: &[u8], threshold: f64) -> bool {
    shannon_entropy(data) < threshold
}
