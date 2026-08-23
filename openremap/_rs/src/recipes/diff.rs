//! Byte-level diff engine — find and merge changed regions between two binaries.
//!
//! Compares only the overlapping prefix — ``min(original.len(),
//! modified.len())`` bytes — the tail of a longer binary is ignored by
//! design.  The recipe pipeline enforces strict size equality before this
//! function is reached (``ECUDiffAnalyzer.check_size_match``, Guard 1 in
//! ``build_recipe()``); direct callers must perform that check themselves.
//!
//! Returns a list of ``(offset, size, ob_bytes, mb_bytes)`` tuples — one per
//! merged block.  The caller is responsible for hex-encoding, building
//! context anchors, and constructing Change objects.
//!
//! Semantics (as the original pure-Python reference behaved):
//!
//! .. code-block:: python
//!
//!     diff_positions = [i for i in range(min_len)
//!                       if original[i] != modified[i]]
//!     # merge positions within merge_threshold into blocks
//!     for blk_start, blk_end in blocks:
//!         ob = original[blk_start:blk_end+1]
//!         mb = modified[blk_start:blk_end+1]

use pyo3::prelude::*;

/// Find all regions where `original` and `modified` differ, merging gaps
/// of up to `merge_threshold` bytes into a single contiguous block.
///
/// Returns a list of ``(offset, size, ob_bytes, mb_bytes)`` tuples — one per
/// merged block.  The caller is responsible for hex-encoding, building
/// context anchors, and constructing Change objects.
///
/// Equivalent to the Python:
///
/// .. code-block:: python
///
///     diff_positions = [i for i in range(min_len)
///                       if original[i] != modified[i]]
///     # merge positions within merge_threshold into blocks
///     for blk_start, blk_end in blocks:
///         ob = original[blk_start:blk_end+1]
///         mb = modified[blk_start:blk_end+1]
#[pyfunction]
#[pyo3(signature = (original, modified, merge_threshold = 16))]
pub fn find_changed_blocks(
    original: &[u8],
    modified: &[u8],
    merge_threshold: usize,
) -> Vec<(usize, usize, Vec<u8>, Vec<u8>)> {
    let min_len = original.len().min(modified.len());

    // --- Step 1: collect differing positions ---
    // Single-pass comparison, push indices directly into a Vec.
    let mut positions: Vec<usize> = Vec::with_capacity(min_len / 100); // ~1% diffs typical
    for i in 0..min_len {
        if original[i] != modified[i] {
            positions.push(i);
        }
    }

    if positions.is_empty() {
        return Vec::new();
    }

    // --- Step 2: merge nearby positions into contiguous blocks ---
    struct Block {
        start: usize,
        end: usize,
    }

    let mut blocks: Vec<Block> = Vec::with_capacity(positions.len() / 4);
    let mut start = positions[0];
    let mut end = positions[0];

    for &pos in &positions[1..] {
        if pos - end <= merge_threshold {
            end = pos;
        } else {
            blocks.push(Block { start, end });
            start = pos;
            end = pos;
        }
    }
    blocks.push(Block { start, end });

    // --- Step 3: extract ob/mb bytes for each block ---
    blocks
        .into_iter()
        .map(|b| {
            let size = b.end - b.start + 1;
            let ob = original[b.start..=b.end].to_vec();
            let mb = modified[b.start..=b.end].to_vec();
            (b.start, size, ob, mb)
        })
        .collect()
}
