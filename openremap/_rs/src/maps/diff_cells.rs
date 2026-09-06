//! diff-maps cell machinery — reading, aligning, and diffing calibration
//! table cell grids.
//!
//! 1:1 port of the pure-Python helpers that lived in
//! ``openremap/core/cli/commands/diff_maps.py`` (plus the shared ``_read_cells``
//! primitive that ``map_classifier.py`` and ``map_exporter.py`` duplicated with
//! slightly different signatures).  The Python side now keeps thin wrappers;
//! the per-cell loops live here.
//!
//! Parity notes:
//!
//! * ``read_cells`` returns the same ``list[int]`` the Python ``_read_cells``
//!   produced (u8 values for ``cell_width == 1``, u16 otherwise, read
//!   little- or big-endian).  Contiguous rows are read in one pass; a
//!   ``stride`` larger than ``cols * cell_width`` means a compound
//!   (interleaved) table half, read row-by-row at ``offset + r * stride``.
//! * ``best_alignment`` mirrors the pad-search: 5×5 shifts around both
//!   guessed offsets, fewest changed cells wins, ties broken by
//!   (distance, stock_offset, tuned_offset) ascending — exactly the Python
//!   ``candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]))`` then
//!   ``candidates[0]``.  The winning pair's cells are returned (the Python
//!   re-read them; keeping them is byte-identical and avoids a re-read).
//! * ``diff_cells`` returns the *unrounded* statistics; the Python wrapper
//!   applies ``round(..., 2)`` and the ``±inf`` handling, so the final
//!   dict is bit-identical to the original implementation (including the
//!   float rounding, which Python's ``round`` does and is deliberately not
//!   reproduced in Rust).

use pyo3::prelude::*;

/// Read a single u16 cell at *offset* (assumes two bytes are available).
#[inline]
fn read_u16(data: &[u8], offset: usize, le: bool) -> u16 {
    let b: [u8; 2] = [data[offset], data[offset + 1]];
    if le {
        u16::from_le_bytes(b)
    } else {
        u16::from_be_bytes(b)
    }
}

/// Decode a rectangular block of cells.
///
/// ``cell_width == 1`` reads raw bytes; any other width reads u16 values
/// (2 bytes per cell, matching ``struct.unpack_from`` with ``H``).  A
/// ``stride`` of ``None`` (or equal to ``cols * cell_width``) reads
/// contiguous rows; otherwise each row starts at ``offset + r * stride``.
///
/// Offsets are assumed valid (all callers pass scanner-validated offsets);
/// out-of-bounds reads simply stop early, mirroring the Python byte-path
/// slice truncation rather than panicking.
fn read_cells_values(
    data: &[u8],
    offset: usize,
    cols: usize,
    rows: usize,
    cell_width: usize,
    le: bool,
    stride: Option<usize>,
) -> Vec<u32> {
    let count = cols * rows;
    let row_bytes = cols * cell_width;
    let mut out: Vec<u32> = Vec::with_capacity(count);

    if stride.is_none() || stride == Some(row_bytes) {
        if cell_width == 1 {
            let end = (offset + count).min(data.len());
            out.extend(data[offset..end].iter().map(|&b| b as u32));
        } else {
            for i in 0..count {
                let pos = offset + i * 2;
                if pos + 2 > data.len() {
                    break;
                }
                out.push(read_u16(data, pos, le) as u32);
            }
        }
    } else {
        let stride = stride.unwrap();
        for r in 0..rows {
            let row_off = offset + r * stride;
            if cell_width == 1 {
                let end = (row_off + cols).min(data.len());
                out.extend(data[row_off..end].iter().map(|&b| b as u32));
            } else {
                for c in 0..cols {
                    let pos = row_off + c * 2;
                    if pos + 2 > data.len() {
                        break;
                    }
                    out.push(read_u16(data, pos, le) as u32);
                }
            }
        }
    }
    out
}

/// Read cell values from the data block at *offset*.
///
/// Python: ``diff_maps._read_cells(data, offset, cols, rows, cell_width,
/// byte_order, stride)`` — also backs ``map_classifier._read_cells`` and
/// ``map_exporter._read_cells`` via their wrappers.
#[pyfunction]
#[pyo3(signature = (data, offset, cols, rows, cell_width, byte_order, stride = None))]
pub fn read_cells(
    data: &[u8],
    offset: usize,
    cols: usize,
    rows: usize,
    cell_width: usize,
    byte_order: &str,
    stride: Option<usize>,
) -> Vec<u32> {
    read_cells_values(data, offset, cols, rows, cell_width, byte_order == "little", stride)
}

/// Find the grid alignment with the fewest changed cells.
///
/// Tries all pad variants (``-4, -2, 0, +2, +4``) around both guessed
/// offsets and keeps the alignment minimising ``(changed, distance,
/// stock_offset, tuned_offset)`` in ascending order.  Returns
/// ``(stock_cells, tuned_cells, stock_offset, tuned_offset)``, or ``None``
/// when no candidate fits (the Python code raised ``IndexError`` there;
/// the wrapper preserves that).
#[pyfunction]
#[pyo3(signature = (stock_data, tuned_data, stock_guess, tuned_guess, cols, rows, cell_width, byte_order, stride = None))]
pub fn best_alignment(
    stock_data: &[u8],
    tuned_data: &[u8],
    stock_guess: isize,
    tuned_guess: isize,
    cols: usize,
    rows: usize,
    cell_width: usize,
    byte_order: &str,
    stride: Option<usize>,
) -> Option<(Vec<u32>, Vec<u32>, isize, isize)> {
    let le = byte_order == "little";
    let count = cols * rows;
    let row_bytes = cols * cell_width;
    let span = if stride.is_none() {
        count * cell_width
    } else {
        rows.saturating_sub(1) * stride.unwrap() + row_bytes
    };

    // (changed, distance, stock_offset, tuned_offset) — the winning cells are
    // re-read once at the end, exactly as the Python implementation did.
    let mut best: Option<(usize, usize, isize, isize)> = None;

    for s_shift in [-4isize, -2, 0, 2, 4] {
        let so = stock_guess + s_shift;
        if so < 0 || (so as usize) + span > stock_data.len() {
            continue;
        }
        let stock_cells = read_cells_values(
            stock_data, so as usize, cols, rows, cell_width, le, stride,
        );
        for t_shift in [-4isize, -2, 0, 2, 4] {
            let to = tuned_guess + t_shift;
            if to < 0 || (to as usize) + span > tuned_data.len() {
                continue;
            }
            let tuned_cells = read_cells_values(
                tuned_data, to as usize, cols, rows, cell_width, le, stride,
            );
            let changed = stock_cells
                .iter()
                .zip(&tuned_cells)
                .filter(|(s, t)| s != t)
                .count();
            let distance = ((so - stock_guess).abs() + (to - tuned_guess).abs()) as usize;
            let key = (changed, distance, so, to);
            let replace = match &best {
                None => true,
                Some((bc, bd, bs, bt)) => key < (*bc, *bd, *bs, *bt),
            };
            if replace {
                best = Some(key);
            }
        }
    }

    let (_changed, _distance, so, to) = best?;
    Some((
        read_cells_values(stock_data, so as usize, cols, rows, cell_width, le, stride),
        read_cells_values(tuned_data, to as usize, cols, rows, cell_width, le, stride),
        so,
        to,
    ))
}

/// Compute cell-by-cell diff statistics between two cell lists.
///
/// Returns ``(max_abs, avg_abs, max_pct, avg_pct, changed_cells,
/// total_cells)`` — *unrounded*; the Python wrapper rounds and applies the
/// ``±inf`` percentage rules exactly as the original implementation did.
/// The caller guarantees equal-length inputs (the wrapper checks first).
#[pyfunction]
pub fn diff_cells(
    stock_cells: Vec<i64>,
    tuned_cells: Vec<i64>,
) -> (f64, f64, f64, f64, usize, usize) {
    let total = stock_cells.len();

    let mut abs_sum: i64 = 0;
    let mut max_abs: i64 = 0;
    let mut changed: usize = 0;
    let mut pct_diffs: Vec<f64> = Vec::with_capacity(total);

    for (&s, &t) in stock_cells.iter().zip(tuned_cells.iter()) {
        let d = (t - s).abs();
        abs_sum += d;
        if d > max_abs {
            max_abs = d;
        }
        if d > 0 {
            changed += 1;
        }

        // Percentage change — handle division by zero.
        // stock=0, tuned≠0  →  ±inf;  stock=0, tuned=0  →  0.0.
        let p = if s != 0 {
            (t - s) as f64 / s as f64 * 100.0
        } else if t != 0 {
            if t > s {
                f64::INFINITY
            } else {
                f64::NEG_INFINITY
            }
        } else {
            0.0
        };
        pct_diffs.push(p);
    }

    // max_pct: the first ±inf wins; otherwise the value with the largest
    // absolute magnitude (ties keep the earlier one, via strict >).
    let mut max_pct: f64 = 0.0;
    for &d in &pct_diffs {
        if d == f64::INFINITY || d == f64::NEG_INFINITY {
            max_pct = d;
            break;
        }
        if d.abs() > max_pct.abs() {
            max_pct = d;
        }
    }

    // avg_pct: mean of the |finite| percentage changes, in order.
    let mut finite_sum = 0.0f64;
    let mut finite_n = 0usize;
    for &d in &pct_diffs {
        if d != f64::INFINITY && d != f64::NEG_INFINITY {
            finite_sum += d.abs();
            finite_n += 1;
        }
    }
    let avg_pct: f64 = if finite_n > 0 {
        finite_sum / finite_n as f64
    } else if !pct_diffs.is_empty() {
        f64::INFINITY
    } else {
        0.0
    };

    let avg_abs: f64 = if total > 0 {
        abs_sum as f64 / total as f64
    } else {
        0.0
    };

    (max_abs as f64, avg_abs, max_pct, avg_pct, changed, total)
}
