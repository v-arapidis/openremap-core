//! Ident-block scanner — 1:1 port of `layout.find_ident_blocks`.
//!
//! Locates exact byte ranges of readable ASCII (printable runs 0x20-0x7E)
//! of at least `min_run` bytes.  For each run the dominant byte + count
//! and the Shannon entropy are returned; the Python layer builds the
//! `Region` dataclass objects and applies its rounding.
//!
//! Dominant-byte semantics mirror CPython's
//! `Counter(chunk).most_common(1)[0]`: the byte with the highest count
//! wins; ties are broken by first occurrence in the chunk (Counter is
//! insertion-ordered and `heapq.nlargest(1, …)` returns the first
//! maximum).

use pyo3::prelude::*;

use crate::primitives::entropy;

#[pyfunction]
pub fn find_ident_blocks(
    data: &[u8],
    min_run: usize,
) -> Vec<(usize, usize, u8, usize, f64)> {
    let mut out: Vec<(usize, usize, u8, usize, f64)> = Vec::new();
    let n = data.len();
    let mut i = 0usize;

    while i < n {
        if !(0x20..=0x7E).contains(&data[i]) {
            i += 1;
            continue;
        }
        let start = i;
        let mut end = i;
        while end < n && (0x20..=0x7E).contains(&data[end]) {
            end += 1;
        }

        if end - start >= min_run {
            let chunk = &data[start..end];
            let mut counts = [0usize; 256];
            let mut first_seen = [usize::MAX; 256];
            for (k, &byte) in chunk.iter().enumerate() {
                counts[byte as usize] += 1;
                if first_seen[byte as usize] == usize::MAX {
                    first_seen[byte as usize] = k;
                }
            }

            // Dominant byte: max count; ties → smallest first occurrence
            // (Counter / nlargest(1) insertion-order semantics).
            let mut dom = 0u8;
            let mut best_count = 0usize;
            let mut best_first = usize::MAX;
            for byte in 0u8..=255u8 {
                let c = counts[byte as usize];
                if c == 0 {
                    continue;
                }
                let f = first_seen[byte as usize];
                if c > best_count || (c == best_count && f < best_first) {
                    dom = byte;
                    best_count = c;
                    best_first = f;
                }
            }

            let ent = entropy::shannon_entropy(chunk);
            out.push((start, end, dom, best_count, ent));
        }
        i = end;
    }

    out
}
