//! Shannon entropy and context-anchor uniqueness search.
//!
//! Each function here is a 1:1 port of its Python counterpart in
//! `openremap.core.services.entropy`.  The Python implementation is the
//! specification; any divergence in output for identical input is a bug.
//!
//! Floating-point: Python's `math.log` and Rust's `f64::ln` may differ at
//! the 15th decimal place.  Both implementations round to 4 decimal places
//! for entropy values, which absorbs any ULP-level discrepancy.

use pyo3::prelude::*;

// ---------------------------------------------------------------------------
// shannon_entropy
// ---------------------------------------------------------------------------

/// Shannon entropy in bits per byte, rounded to 4 decimal places.
///
/// Returns 0.0 for empty data or perfectly uniform data (all zeros, etc.).
/// Returns up to 8.0 for perfectly random data.
///
/// Uses a `[u32; 256]` frequency array — no heap allocations, no hashing.
/// Equivalent to Python's ``collections.Counter(data)`` but ~20–50× faster.
#[pyfunction]
#[pyo3(signature = (data))]
pub fn shannon_entropy(data: &[u8]) -> f64 {
    if data.is_empty() {
        return 0.0;
    }

    let total = data.len() as f64;

    // Fixed-size frequency table — byte values are 0..=255.
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

    // Convert from natural log to log2, then round to 4 decimal places.
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

// ---------------------------------------------------------------------------
// count_unique_in_window
// ---------------------------------------------------------------------------

/// Count all occurrences of `needle` within a bounded region of `haystack`.
///
/// Uses a sliding search with ``pos += 1`` after each match — identical to
/// Python's ``bytes.find()`` loop, which finds overlapping occurrences.
/// Returns 0 when `needle` is empty.
///
/// Equivalent to:
///
/// .. code-block:: python
///
///     region = haystack[window_start:window_end]
///     count = 0; pos = 0
///     while True:
///         p = region.find(needle, pos)
///         if p == -1: break
///         count += 1; pos = p + 1
#[pyfunction]
#[pyo3(signature = (haystack, needle, window_start, window_end))]
pub fn count_unique_in_window(
    haystack: &[u8],
    needle: &[u8],
    window_start: usize,
    window_end: usize,
) -> usize {
    if needle.is_empty() {
        return 0;
    }

    let window_end = window_end.min(haystack.len());
    if window_start >= window_end {
        return 0;
    }

    let region = &haystack[window_start..window_end];
    let mut count = 0usize;
    let mut pos = 0usize;

    while let Some(p) = region[pos..]
        .windows(needle.len())
        .position(|w| w == needle)
    {
        count += 1;
        pos += p + 1; // step forward by one byte past the match (overlapping)
    }

    count
}

// ---------------------------------------------------------------------------
// find_unique_context
// ---------------------------------------------------------------------------

/// Find a context anchor before `change_offset` whose `ctx + ob` pattern
/// is unique in the entire binary AND has entropy ≥ `entropy_threshold`.
///
/// The window doubles geometrically (32 → 64 → 128 → 256 → 512) until both
/// conditions are satisfied or `max_size` is reached.
///
/// Returns ``(context_bytes, context_size, entropy, match_count)`` as a
/// 4-tuple — same shape as the Python function.
///
/// Raises ``ValueError`` when `change_offset` is out of bounds.
#[pyfunction]
#[pyo3(signature = (data, change_offset, change_size, ob, min_size = 32, max_size = 512, entropy_threshold = 2.5))]
pub fn find_unique_context(
    data: &[u8],
    change_offset: isize,
    change_size: isize,
    ob: &[u8],
    min_size: usize,
    max_size: usize,
    entropy_threshold: f64,
) -> PyResult<(Vec<u8>, usize, f64, usize)> {
    let file_len = data.len() as isize;

    // --- Guard: offset must be valid (matches Python) ---
    if change_offset < 0 || change_offset > file_len {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "change_offset {} is out of bounds (file size: {} bytes).",
            change_offset, file_len
        )));
    }

    let offset = change_offset as usize;
    let _change_sz = change_size; // unused after guard, kept for API compatibility

    let mut size = min_size;

    while size <= max_size {
        let ctx_start = if offset >= size { offset - size } else { 0 };
        let ctx = &data[ctx_start..offset];
        let actual_size = ctx.len();

        if actual_size == 0 {
            return Ok((vec![], 0, 0.0, 0));
        }

        let entropy = shannon_entropy(ctx);

        // Build ctx + ob anchor
        let mut anchor = Vec::with_capacity(ctx.len() + ob.len());
        anchor.extend_from_slice(ctx);
        anchor.extend_from_slice(ob);

        let match_count = count_unique_in_window(data, &anchor, 0, data.len());

        if entropy >= entropy_threshold && match_count == 1 {
            return Ok((ctx.to_vec(), actual_size, entropy, match_count));
        }

        size *= 2;
    }

    // --- max_size reached — return best effort ---
    let ctx_start = if offset >= max_size {
        offset - max_size
    } else {
        0
    };
    let ctx = &data[ctx_start..offset];
    let entropy = shannon_entropy(ctx);

    let mut anchor = Vec::with_capacity(ctx.len() + ob.len());
    anchor.extend_from_slice(ctx);
    anchor.extend_from_slice(ob);

    let match_count = count_unique_in_window(data, &anchor, 0, data.len());

    Ok((ctx.to_vec(), ctx.len(), entropy, match_count))
}
