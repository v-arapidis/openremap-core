//! Calibration map axis and table scanner.
//!
//! 1:1 port of `openremap.core.services.map_hunter`.  The Python implementation
//! is the specification — oracle fuzz tests verify identical output.

use pyo3::prelude::*;
use std::collections::{HashMap, HashSet};

// ---------------------------------------------------------------------------
// Constants (mirror map_hunter.py)
// ---------------------------------------------------------------------------

const SKIP_WINDOW: usize = 8;
const MIN_TABLE_CELLS: usize = 8;
const TABLE_MAX_VALUE_U16: u32 = 0xF000;
const TABLE_MAX_VALUE_U8: u32 = 0xF0;
const TABLE_TRIVIAL_FRACTION: f64 = 0.30;
const TABLE_SENTINEL_FRACTION: f64 = 0.45;
const TABLE_MIN_DISTINCT_RATIO: f64 = 0.18;
const TABLE_ASCII_FRACTION: f64 = 0.75;
const PADDING_OFFSETS: [usize; 3] = [0, 2, 4];
const MAX_Y_LEN: usize = 32;
const MAX_SERIES_TABLES: usize = 16;
const COMMON_AXIS_SIZES: std::ops::RangeInclusive<usize> = 4..=32;

const SENTINELS_U16: [u16; 4] = [0x0000, 0xFFFF, 0x8000, 0x7FFF];
const SENTINELS_U8: [u8; 4] = [0x00, 0xFF, 0x80, 0x7F];
const ERASURES_U16: [u16; 2] = [0x0000, 0xFFFF];
const ERASURES_U8: [u8; 2] = [0x00, 0xFF];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn read_u16(data: &[u8], offset: usize, le: bool) -> Option<u16> {
    let b: [u8; 2] = data.get(offset..offset + 2)?.try_into().ok()?;
    Some(if le { u16::from_le_bytes(b) } else { u16::from_be_bytes(b) })
}

fn is_trivial_block(data: &[u8], start: usize, length: usize) -> bool {
    let end = (start + length).min(data.len());
    if end <= start { return true; }
    let first = data[start];
    if first != 0x00 && first != 0xFF { return false; }
    data[start..end].iter().all(|&b| b == first)
}

fn try_axis_at(data: &[u8], offset: usize, le: bool, min_len: usize,
               max_len: usize, min_step: i32, max_step: i32) -> usize {
    let mut prev = match read_u16(data, offset, le) { Some(v) => v as i32, None => return 0 };
    let limit = (offset + max_len * 2).min(data.len());
    let mut pos = offset + 2;
    let mut count = 1;
    while pos + 1 < limit {
        let cur = match read_u16(data, pos, le) { Some(v) => v as i32, None => break };
        let diff = cur - prev;
        if diff < min_step || diff > max_step { break; }
        prev = cur; pos += 2; count += 1;
    }
    count
}

fn value_plausibility(values: &[u16]) -> f64 {
    let lo = *values.iter().min().unwrap_or(&0) as f64;
    let hi = *values.iter().max().unwrap_or(&0) as f64;
    let span = hi - lo;
    // Axes that start at or near zero are always plausible.
    if span <= 0.0 || lo <= 0.0 { return 1.0; }
    let ratio = span / lo;
    if ratio < 0.05 { return 0.25; }  // barely moves — memory addresses
    if ratio < 0.15 { return 0.60; }  // suspiciously tight cluster
    1.0
}

fn axis_quality(values: &[u16]) -> f64 {
    let n = values.len();
    if n < 2 { return 0.0; }
    let steps: Vec<f64> = values.windows(2).map(|w| w[1] as f64 - w[0] as f64).collect();
    let mean = steps.iter().sum::<f64>() / steps.len() as f64;
    if mean <= 0.0 { return 0.0; }
    let (min_s, max_s) = steps.iter().fold((f64::MAX, f64::MIN),
        |(lo, hi), &s| (lo.min(s), hi.max(s)));
    let linearity = (1.0 - (max_s - min_s) / (mean * 4.0)).max(0.0);
    let bonus = if COMMON_AXIS_SIZES.contains(&n) { 1.0 } else { 0.7 };
    let raw = 0.6 * linearity + 0.4 * bonus;
    raw * value_plausibility(values)
}

fn cell_max(cw: usize) -> u32 { if cw == 1 { TABLE_MAX_VALUE_U8 } else { TABLE_MAX_VALUE_U16 } }

fn is_sentinel(v: u32, cw: usize) -> bool {
    if cw == 1 { SENTINELS_U8.contains(&(v as u8)) } else { SENTINELS_U16.contains(&(v as u16)) }
}

fn is_erasure(v: u32, cw: usize) -> bool {
    if cw == 1 { ERASURES_U8.contains(&(v as u8)) } else { ERASURES_U16.contains(&(v as u16)) }
}

fn read_block(data: &[u8], offset: usize, count: usize, le: bool, cw: usize) -> Option<Vec<u32>> {
    let end = offset + count * cw;
    if end > data.len() { return None; }
    let mut vals = Vec::with_capacity(count);
    if cw == 1 {
        for &b in &data[offset..end] { vals.push(b as u32); }
    } else {
        for i in 0..count {
            vals.push(read_u16(data, offset + i * 2, le)? as u32);
        }
    }
    Some(vals)
}

fn is_ascii_dense(data: &[u8], offset: usize, length: usize) -> bool {
    let end = (offset + length).min(data.len());
    if end <= offset { return false; }
    let printable = data[offset..end].iter().filter(|&&b| (0x20..=0x7E).contains(&b)).count();
    (printable as f64) / ((end - offset) as f64) >= TABLE_ASCII_FRACTION
}

fn quick_trivial_fraction(data: &[u8], offset: usize, byte_count: usize) -> f64 {
    let end = (offset + byte_count).min(data.len());
    if end <= offset { return 1.0; }
    let chunk = &data[offset..end];
    let z = chunk.iter().filter(|&&b| b == 0).count();
    let f = chunk.iter().filter(|&&b| b == 0xFF).count();
    (z + f) as f64 / chunk.len() as f64
}

fn is_clearly_erased(data: &[u8], offset: usize, byte_count: usize, cw: usize) -> bool {
    let frac = quick_trivial_fraction(data, offset, byte_count);
    if cw == 1 { frac > TABLE_TRIVIAL_FRACTION } else { frac > 0.70 }
}

fn line_smoothness(line: &[u32]) -> (f64, bool, i32) {
    let n = line.len();
    if n < 2 { return (0.5, false, 0); }
    let (mut lo, mut hi) = (line[0] as i64, line[0] as i64);
    let mut step_sum: i64 = 0;
    let mut inc = true; let mut dec = true;
    let mut prev = line[0] as i64;
    for &v in &line[1..] {
        let v = v as i64;
        if v < lo { lo = v; } else if v > hi { hi = v; }
        let d = v - prev;
        if d < 0 { inc = false; step_sum -= d; }
        else { if d > 0 { dec = false; } step_sum += d; }
        prev = v;
    }
    if hi == lo { return (0.5, true, 0); }
    let mean_step = step_sum as f64 / (n - 1) as f64;
    let spread = (hi - lo) as f64;
    let mut smooth = 1.0 - mean_step / spread;
    if smooth < 0.0 { smooth = 0.0; }
    let dir = if inc { 1 } else if dec { -1 } else { 0 };
    (smooth, false, dir)
}

fn stripe_penalty(values: &[u32], cols: usize, distinct_count: Option<usize>) -> f64 {
    let n = values.len();
    if cols == 0 { return 1.0; }
    let rows = n / cols;
    let small = n <= 25;

    if rows >= 2 {
        let first = &values[..cols];
        let mut equal = 0;
        for r in 1..rows {
            if &values[r * cols..(r + 1) * cols] == first { equal += 1; }
        }
        if equal >= rows - 1 { return 0.0; }
        if equal >= rows / 2 { return if small { 0.3 } else { 0.4 }; }
    }

    if let Some(dc) = distinct_count {
        if dc > n / 2 && !(small && dc <= 4usize.max(n / 3)) {
            return 1.0;
        }
    }

    let max_period = (n / 3).min(12);
    let hi_thr = if small { 0.85 } else { 0.9 };
    let mid_thr = if small { 0.65 } else { 0.75 };

    for period in 2..=max_period {
        if n < period * 3 { break; }
        let mut matches = 0;
        let limit = n - period;
        for i in period..n {
            if values[i] == values[i - period] { matches += 1; }
        }
        let ratio = matches as f64 / limit as f64;
        if ratio > hi_thr { return if small { 0.15 } else { 0.2 }; }
        if ratio > mid_thr { return if small { 0.4 } else { 0.5 }; }
    }
    1.0
}

fn sentinel_penalty(non_trivial: f64) -> f64 {
    let sentinel_frac = 1.0 - non_trivial;
    // Only penalise above 25% — real clamp / limit tables legitimately
    // contain sentinel markers (0xFFFF = "no limit", 0x0000 = "disabled")
    // at up to ~20% of cells.
    if sentinel_frac > 0.40 { return 0.40; }
    if sentinel_frac > 0.30 { return 0.60; }
    if sentinel_frac > 0.25 { return 0.80; }
    1.0
}

fn score_table_block(values: &[u32], cols: usize, cw: usize) -> f64 {
    score_table_block_impl(values, cols, cw, true)
}

/// Score a block, optionally skipping the stripe penalty.  Flat-Y tables
/// (identical rows by definition) are penalised to zero by the stripe check,
/// so the flat-Y strategy uses this with ``apply_stripe = false``.
fn score_table_block_impl(
    values: &[u32], cols: usize, cw: usize, apply_stripe: bool,
) -> f64 {
    let n = values.len();
    if n == 0 || cols == 0 { return 0.0; }

    let c_max = cell_max(cw);
    let signed_clamp: u32 = if cw == 1 { 0x80 } else { 0x8000 };

    let mut bounded_n = 0;
    let mut non_trivial_n = 0;
    let mut distinct: HashSet<u32> = HashSet::new();
    for &v in values {
        if v <= c_max && v != signed_clamp { bounded_n += 1; }
        if !is_sentinel(v, cw) { non_trivial_n += 1; }
        distinct.insert(v);
    }
    let bounded = bounded_n as f64 / n as f64;
    let non_trivial = non_trivial_n as f64 / n as f64;
    let rows = n / cols;

    // Row smoothness
    let (mut row_smooth, mut row_mono, mut flat_rows) = (0.5f64, 0usize, 0usize);
    if cols >= 2 {
        let total: f64 = (0..rows).map(|r| {
            let (s, flat, dir) = line_smoothness(&values[r * cols..(r + 1) * cols]);
            if flat { /* flat_rows++ handled separately */ }
            if dir != 0 { /* row_mono++ */ }
            s
        }).sum();
        for r in 0..rows {
            let (_, flat, dir) = line_smoothness(&values[r * cols..(r + 1) * cols]);
            if flat { flat_rows += 1; }
            if dir != 0 { row_mono += 1; }
        }
        row_smooth = total / rows as f64;
    }
    let flat_frac = if rows > 0 { flat_rows as f64 / rows as f64 } else { 0.0 };
    row_smooth *= (1.0 - flat_frac).max(0.3);

    // Column smoothness
    let (mut col_smooth, mut col_mono) = (0.5f64, 0usize);
    if rows >= 2 {
        let mut total = 0.0;
        for c in 0..cols {
            let col: Vec<u32> = (0..rows).map(|r| values[r * cols + c]).collect();
            let (s, _, dir) = line_smoothness(&col);
            total += s;
            if dir != 0 { col_mono += 1; }
        }
        col_smooth = total / cols as f64;
    }

    // Monotonic bonus
    let mono_score = if rows >= 2 && cols >= 2 {
        (row_mono as f64 / rows as f64).max(col_mono as f64 / cols as f64)
    } else if cols >= 2 {
        row_mono as f64 / (rows.max(1) as f64)
    } else if rows >= 2 {
        col_mono as f64 / (cols.max(1) as f64)
    } else { 0.0 };

    let distinct_count = distinct.len();
    // Integer-floor sqrt to match Python's int(n**0.5).
    let sqrt_n = (n as f64).sqrt() as usize;
    let distinct_score = (distinct_count as f64 / ((sqrt_n * 2).max(4) as f64)).min(1.0);

    let raw = 0.20 * bounded + 0.20 * non_trivial + 0.15 * row_smooth
        + 0.15 * col_smooth + 0.15 * mono_score + 0.15 * distinct_score;
    if raw < 0.4 { return raw; }
    let stripe = if apply_stripe {
        stripe_penalty(values, cols, Some(distinct_count))
    } else {
        1.0
    };
    raw * stripe * sentinel_penalty(non_trivial)
}

fn block_passes_hard_filters(values: &[u32], cw: usize) -> bool {
    let n = values.len();
    if n < MIN_TABLE_CELLS { return false; }

    let mut trivial = 0;
    let mut sentinel = 0;
    let trivial_cap = (n as f64 * TABLE_TRIVIAL_FRACTION) as usize + 1;
    let sentinel_cap = (n as f64 * TABLE_SENTINEL_FRACTION) as usize + 1;
    let (mut lo, mut hi) = (values[0], values[0]);
    let mut distinct: HashSet<u32> = HashSet::new();

    for &v in values {
        if is_erasure(v, cw) { trivial += 1; if trivial > trivial_cap { return false; } }
        if is_sentinel(v, cw) { sentinel += 1; if sentinel > sentinel_cap { return false; } }
        if v < lo { lo = v; } else if v > hi { hi = v; }
        distinct.insert(v);
    }
    if lo == hi { return false; }
    let d = distinct.len();
    if (d as f64 / n as f64) < TABLE_MIN_DISTINCT_RATIO && d < 6 { return false; }
    true
}

fn candidate_y_lens(y_values: Option<&[u16]>, y_max_len: usize, min_y: usize) -> Vec<usize> {
    let mut cands: HashSet<usize> = HashSet::new();
    if y_max_len >= min_y { cands.insert(y_max_len); }
    if let Some(yv) = y_values {
        let n = yv.len();
        let steps: Vec<i64> = (0..n - 1).map(|i| yv[i + 1] as i64 - yv[i] as i64).collect();
        for k in min_y - 1..steps.len() {
            let window = if k >= 3 { &steps[k - 3..k] } else { &steps[..k] };
            if window.is_empty() { continue; }
            let avg: f64 = window.iter().sum::<i64>() as f64 / window.len() as f64;
            if avg <= 0.0 { continue; }
            let cur = steps[k] as f64;
            if cur >= avg * 4.0 || cur <= avg / 4.0 {
                let ln = k + 1;
                if ln >= min_y && ln <= y_max_len { cands.insert(ln); }
            }
        }
    }
    for &ln in &[4, 5, 6, 8, 10, 12, 16] {
        if ln >= min_y && ln <= y_max_len { cands.insert(ln); }
    }
    let mut v: Vec<usize> = cands.into_iter().collect();
    v.sort_by(|a, b| b.cmp(a)); // descending
    v
}

/// Return type: (offset, cols, rows, cell_width, byte_order, x_axis_offset,
/// y_axis_offset, score, row_stride_bytes) — `stride` is `None` for contiguous
/// data, `Some(cols_total * cell_width)` for compound tables whose two halves
/// interleave per row.
type TableResult = (usize, usize, usize, usize, String, usize, Option<usize>, f64, Option<usize>);

fn effective_min_score(cells: usize, min_score: f64) -> f64 {
    if cells <= 9 { min_score.max(0.78) }
    else if cells <= 16 { min_score.max(0.72) }
    else if cells <= 25 { min_score.max(0.65) }
    else { min_score }
}

fn try_pair_with_y(
    buf: &[u8], x_off: usize, x_len: usize, y_off: usize, y_max_len_raw: usize,
    le: bool, cw: usize, min_y: usize, min_score: f64, x_qual: f64,
    y_values: Option<&[u16]>, max_padding: usize,
) -> Option<TableResult> {
    let y_max_len = y_max_len_raw.min(MAX_Y_LEN);
    if y_max_len < min_y { return None; }

    let buf_len = buf.len();
    let bo = if le { "little" } else { "big" };
    let mut best: Option<TableResult> = None;

    for y_len in candidate_y_lens(y_values, y_max_len, min_y) {
        let y_end = y_off + y_len * 2;
        if y_end > buf_len { continue; }
        let yv: Option<Vec<u16>> = y_values.map(|yv| yv[..y_len].to_vec());
        let y_qual = if let Some(ref v) = yv { axis_quality(v) } else { 0.5 };
        for &pad in PADDING_OFFSETS.iter() {
            if pad > max_padding { break; }
            let data_start = y_end + pad;
            let cells = x_len * y_len;
            let byte_count = cells * cw;
            if data_start + byte_count > buf_len { continue; }
            if is_clearly_erased(buf, data_start, byte_count, cw) { continue; }
            let block = read_block(buf, data_start, cells, le, cw)?;
            if !block_passes_hard_filters(&block, cw) { continue; }
            if cw >= 2 && is_ascii_dense(buf, data_start, byte_count) { continue; }
            let raw = score_table_block(&block, x_len, cw);
            let score = raw * (0.7 + 0.15 * x_qual + 0.15 * y_qual);
            if score < effective_min_score(x_len * y_len, min_score) { continue; }
            let cand = (data_start, x_len, y_len, cw, bo.to_string(), x_off,
                        Some(y_off), score, None);
            if best.as_ref().map_or(true, |b| cand.7 > b.7) { best = Some(cand); }
        }
    }
    best
}

/// Probe forward from *anchor* for additional blocks sharing the same axes.
///
/// After a strategy-1 2D hit (or 1D hit), the bytes immediately after the
/// data block may contain further tables of identical geometry — different
/// calibrations (fuel, timing, boost, EGR) that share the same RPM×Load axes.
///
/// Returns zero or more additional candidates.  The anchor itself is NOT
/// included in the returned slice.
fn probe_table_series(
    buf: &[u8], anchor: &TableResult, le: bool,
    min_score: f64, max_gap: usize, max_series_tables: usize,
) -> Vec<TableResult> {
    if max_series_tables <= 1 { return Vec::new(); }

    let data_start = anchor.0;
    let cols = anchor.1;
    let rows = anchor.2;
    let cw = anchor.3;
    let bo = &anchor.4;
    let x_off = anchor.5;
    let y_off = anchor.6;
    let cells = cols * rows;
    let step_bytes = cells * cw;
    let is_2d = rows > 1;

    // Recompute axis qualities from buf (cheap, ≤32 values).
    let x_qual = {
        let xv: Vec<u16> = (0..cols).map(|k| read_u16(buf, x_off + k * 2, le).unwrap_or(0)).collect();
        axis_quality(&xv)
    };
    let y_qual: f64 = if is_2d {
        if let Some(yo) = y_off {
            let yv: Vec<u16> = (0..rows).map(|k| read_u16(buf, yo + k * 2, le).unwrap_or(0)).collect();
            axis_quality(&yv)
        } else { 0.5 }
    } else { 0.5 };

    let mut out = Vec::new();
    let mut pos = data_start + step_bytes;
    let mut count = 1;

    'outer: while count < max_series_tables {
        for &pad in PADDING_OFFSETS.iter() {
            if pad > max_gap { break; }
            let p = pos + pad;
            if p + step_bytes > buf.len() { continue; }
            if is_clearly_erased(buf, p, step_bytes, cw) { continue; }
            let block = match read_block(buf, p, cells, le, cw) {
                Some(b) => b, None => continue,
            };
            if !block_passes_hard_filters(&block, cw) { continue; }
            if cw >= 2 && is_ascii_dense(buf, p, step_bytes) { continue; }

            let raw = score_table_block(&block, cols, cw);
            let score = if is_2d {
                raw * (0.7 + 0.15 * x_qual + 0.15 * y_qual)
            } else {
                raw * (0.7 + 0.15 * x_qual + 0.075)
            };
            if score < effective_min_score(cells, min_score) { continue; }

            out.push((p, cols, rows, cw, bo.to_string(), x_off, y_off, score, None));
            pos = p + step_bytes;
            count += 1;
            continue 'outer;  // advance to next slot, restart pad sweep
        }
        break;  // no pad worked → series ends
    }
    out
}

// ---------------------------------------------------------------------------
// scan_map_axes
// ---------------------------------------------------------------------------

#[pyfunction]
#[pyo3(signature = (data, region_start = -1, region_end = -1,
                    min_axis_length = 4, max_axis_length = 32,
                    min_step = 1, max_step = 10000))]
pub fn scan_map_axes(
    data: &[u8], region_start: isize, region_end: isize,
    min_axis_length: usize, max_axis_length: usize,
    min_step: i32, max_step: i32,
) -> Vec<(usize, usize, String, Vec<u16>)> {
    let base_offset = if region_start >= 0 { region_start as usize } else { 0 };
    let raw_end = if region_end >= 0 { region_end as usize } else { data.len() };
    let start = base_offset.min(data.len());
    let end = raw_end.min(data.len()).max(start);
    let buf = &data[start..end];

    if buf.len() < min_axis_length * 2 { return Vec::new(); }

    let mut claimed: HashSet<usize> = HashSet::new();
    let mut results: Vec<(usize, usize, String, Vec<u16>)> = Vec::new();

    for &le in &[true, false] {
        let bo = if le { "little" } else { "big" };
        let mut offset = 0;
        while offset + min_axis_length * 2 <= buf.len() {
            if is_trivial_block(buf, offset, SKIP_WINDOW) { offset += SKIP_WINDOW; continue; }
            if claimed.contains(&offset) { offset += 2; continue; }

            let run = try_axis_at(buf, offset, le, min_axis_length, max_axis_length, min_step, max_step);
            if run >= min_axis_length {
                let vals: Vec<u16> = (0..run).map(|i| read_u16(buf, offset + i * 2, le).unwrap_or(0)).collect();
                for i in 0..run { claimed.insert(offset + i * 2); }
                results.push((base_offset + offset, run, bo.to_string(), vals));
                offset += run * 2;
            } else { offset += 2; }
        }
    }

    // Collapse contained sub-axes
    if results.len() > 1 {
        let mut by_bo: HashMap<String, Vec<(usize, usize, usize)>> = HashMap::new();
        for (i, (off, len, bo, _)) in results.iter().enumerate() {
            by_bo.entry(bo.clone()).or_default().push((*off, off + len * 2, i));
        }
        let mut drop: HashSet<usize> = HashSet::new();
        for runs in by_bo.values_mut() {
            runs.sort_by_key(|r| (r.0, -(r.1 as isize - r.0 as isize)));
            for i in 0..runs.len() {
                let (s_i, e_i, idx_i) = runs[i];
                for j in 0..i {
                    let (s_j, e_j, _) = runs[j];
                    if e_j <= s_i { continue; }
                    if s_j <= s_i && e_i <= e_j && (e_i - s_i) < (e_j - s_j) {
                        drop.insert(idx_i);
                        break;
                    }
                }
            }
        }
        if !drop.is_empty() {
            results = results.into_iter().enumerate().filter(|(i, _)| !drop.contains(i)).map(|(_, r)| r).collect();
        }
    }
    results
}

// ---------------------------------------------------------------------------
// scan_map_tables
// ---------------------------------------------------------------------------

#[pyfunction]
#[pyo3(signature = (data, region_start = -1, region_end = -1,
                    axes = None, min_score = 0.55, max_gap = 8,
                    min_y_length = 3, min_axis_length = 4,
                    cell_widths = vec![2usize, 1], max_results = 8000,
                    max_series_tables = 16))]
pub fn scan_map_tables(
    data: &[u8], region_start: isize, region_end: isize,
    axes: Option<Vec<(usize, usize, String, Vec<u16>)>>,
    min_score: f64, max_gap: usize, min_y_length: usize,
    min_axis_length: usize, cell_widths: Vec<usize>, max_results: usize,
    max_series_tables: usize,
) -> Vec<(usize, usize, usize, usize, String, usize, Option<usize>, f64, Option<usize>)> {
    let base_offset = if region_start >= 0 { region_start as usize } else { 0 };
    let raw_end = if region_end >= 0 { region_end as usize } else { data.len() };
    let start = base_offset.min(data.len());
    let end = raw_end.min(data.len()).max(start);
    let buf = &data[start..end];

    let axes_list = axes.unwrap_or_else(|| {
        scan_map_axes(buf, -1, -1, min_axis_length, 32, 1, 10000)
    });

    if axes_list.is_empty() { return Vec::new(); }

    let mut by_order: HashMap<String, Vec<(usize, usize, Vec<u16>)>> = HashMap::new();
    for (off, len, bo, vals) in &axes_list {
        by_order.entry(bo.clone()).or_default().push((*off, *len, vals.clone()));
    }
    for v in by_order.values_mut() { v.sort_by_key(|a| a.0); }

    let mut candidates: Vec<TableResult> = Vec::new();

    for (bo, ordered) in &by_order {
        let le = bo == "little";
        for (i, &(x_off, x_len, ref x_vals)) in ordered.iter().enumerate() {
            let x_qual = axis_quality(x_vals);
            let x_end_full = x_off + x_len * 2;

            for &cw in &cell_widths {
                let mut strategy1_hit = false;

                // Strategy 1: standard pairing
                for &(y_off, y_len, ref y_vals) in &ordered[i + 1..] {
                    let gap = if y_off >= x_end_full { y_off - x_end_full } else { continue };
                    if gap > max_gap { break; }
                    if let Some(cand) = try_pair_with_y(buf, x_off, x_len, y_off, y_len,
                        le, cw, min_y_length, min_score, x_qual, Some(y_vals), max_gap) {
                        strategy1_hit = true;
                        // Probe for shared-axis blocks — must do this before
                        // moving cand into candidates (Rust ownership).
                        if max_series_tables > 1 {
                            candidates.extend(probe_table_series(buf, &cand, le, min_score,
                                max_gap, max_series_tables));
                        }
                        candidates.push(cand);
                    }
                }

                if strategy1_hit { continue; }

                // Strategy 2: X truncation
                let n_trunc = max_gap.min(x_len.saturating_sub(min_axis_length));
                for trunc in 1..=n_trunc {
                    let xl = x_len - trunc;
                    if xl < min_axis_length { break; }
                    let x_end_t = x_off + xl * 2;
                    let x_vals_t = &x_vals[..xl];
                    let x_qual_t = axis_quality(x_vals_t);

                    // 2a: pair shorter X with existing Y
                    for &(y_off, y_len, ref y_vals) in &ordered[i + 1..] {
                        let gap_t = if y_off >= x_end_t { y_off - x_end_t } else { continue };
                        if gap_t > max_gap { break; }
                        if let Some(cand) = try_pair_with_y(buf, x_off, xl, y_off, y_len,
                            le, cw, min_y_length, min_score, x_qual_t, Some(y_vals), max_gap) {
                            candidates.push(cand);
                        }
                    }

                    // 2b: re-scan for absorbed Y
                    let run = try_axis_at(buf, x_end_t, le, min_y_length, MAX_Y_LEN, 1, 10000);
                    if run >= min_y_length {
                        let yv: Vec<u16> = (0..run).map(|k| read_u16(buf, x_end_t + k * 2, le).unwrap_or(0)).collect();
                        if let Some(cand) = try_pair_with_y(buf, x_off, xl, x_end_t, run,
                            le, cw, min_y_length, min_score, x_qual_t, Some(&yv), max_gap) {
                            candidates.push(cand);
                        }
                    }
                }

                // 1D candidate
                for &pad in PADDING_OFFSETS.iter() {
                    if pad > max_gap { break; }
                    let d_start = x_end_full + pad;
                    let byte_count = x_len * cw;
                    if d_start + byte_count > buf.len() { continue; }
                    if is_clearly_erased(buf, d_start, byte_count, cw) { continue; }
                    let vals = match read_block(buf, d_start, x_len, le, cw) { Some(v) => v, None => continue };
                    if !block_passes_hard_filters(&vals, cw) { continue; }
                    if cw >= 2 && is_ascii_dense(buf, d_start, byte_count) { continue; }
                    let raw = score_table_block(&vals, x_len, cw);
                    let score = raw * (0.7 + 0.15 * x_qual + 0.075);
                    if score < effective_min_score(x_len, min_score) { continue; }
                    let cand = (d_start, x_len, 1, cw, bo.clone(), x_off, None, score, None);
                    // Probe before push — Rust ownership.
                    if max_series_tables > 1 {
                        candidates.extend(probe_table_series(buf, &cand, le, min_score,
                            max_gap, max_series_tables));
                    }
                    candidates.push(cand);
                }

                // Flat-Y candidate — X axis followed by N identical rows.
                // Real ECUs store maps that are constant along the load
                // axis this way (e.g. flat limiters): there is no monotonic
                // Y axis, and with < 8 cells per row the 1D path above
                // cannot see them either (MIN_TABLE_CELLS).
                for &pad in PADDING_OFFSETS.iter() {
                    if pad > max_gap { break; }
                    let d_start = x_end_full + pad;
                    if d_start + x_len * cw > buf.len() { continue; }
                    if is_clearly_erased(buf, d_start, x_len * cw, cw) { continue; }
                    let row0 = match read_block(buf, d_start, x_len, le, cw) {
                        Some(v) => v, None => continue,
                    };
                    if row0.len() < 4 { continue; }
                    // Count identical consecutive rows.
                    let mut n_rows = 1usize;
                    let mut pos = d_start + x_len * cw;
                    while n_rows < MAX_Y_LEN
                        && pos + x_len * cw <= buf.len()
                    {
                        let row = match read_block(buf, pos, x_len, le, cw) {
                            Some(v) => v, None => break,
                        };
                        if row != row0 { break; }
                        n_rows += 1;
                        pos += x_len * cw;
                    }
                    if n_rows < 2 { continue; }
                    let cells = x_len * n_rows;
                    if cells < MIN_TABLE_CELLS { continue; }
                    if cw >= 2 && is_ascii_dense(buf, d_start, cells * cw) { continue; }
                    let block = match read_block(buf, d_start, cells, le, cw) {
                        Some(v) => v, None => continue,
                    };
                    // Identical rows trip the stripe penalty by design —
                    // score without it, but keep sentinel/erasure checks.
                    let raw = score_table_block_impl(&block, x_len, cw, false);
                    let score = raw * (0.7 + 0.15 * x_qual + 0.075);
                    if score < effective_min_score(cells, min_score) { continue; }
                    let cand = (
                        d_start, x_len, n_rows, cw, bo.clone(), x_off, None,
                        score, None,
                    );
                    candidates.push(cand);
                }
            }
        }
    }

    // Adjust offsets to be absolute within the original file.
    if base_offset > 0 {
        for c in &mut candidates {
            c.0 += base_offset; // data offset
            c.5 += base_offset; // x_axis_offset
            if let Some(ref mut yo) = c.6 { *yo += base_offset; }
        }
    }

    // Dedup — sort by score, area, 2D-over-1D, offset
    candidates.sort_by(|a, b| {
        round2(a.7).partial_cmp(&round2(b.7)).unwrap().reverse()
            .then(((a.1 * a.2) as isize).cmp(&((b.1 * b.2) as isize)).reverse())
            .then((if a.2 > 1 { 1 } else { 0 }).cmp(&(if b.2 > 1 { 1 } else { 0 })).reverse())
            .then(a.0.cmp(&b.0))
    });

    fn footprint(t: &TableResult) -> (usize, usize) {
        let data_end = t.0 + t.1 * t.2 * t.3;
        let mut start = t.5; // x_axis_offset
        if let Some(yo) = t.6 { start = start.min(yo); }
        start = start.min(t.0);
        let mut end = data_end.max(t.5 + t.1 * 2);
        if let Some(yo) = t.6 { end = end.max(yo + t.2 * 2); }
        (start, end)
    }

    fn round2(x: f64) -> f64 { (x * 100.0 + 0.5).floor() / 100.0 }

    // ClaimId groups tables that share the same axes — series members
    // (from probe_table_series) with identical geometry but disjoint data
    // ranges must survive the footprint dedup.
    fn claim_id(t: &TableResult) -> (usize, Option<usize>, usize, String) {
        (t.5, t.6, t.3, t.4.clone())  // (x_axis_offset, y_axis_offset, cell_width, byte_order)
    }

    struct Claim { fs: usize, fe: usize, ds: usize, de: usize, cid: (usize, Option<usize>, usize, String) }

    let mut claims: Vec<Claim> = Vec::new();
    let mut chosen: Vec<TableResult> = Vec::new();

    for cand in candidates {
        let (fs, fe) = footprint(&cand);
        let (ds, de) = (cand.0, cand.0 + cand.1 * cand.2 * cand.3);
        let cid = claim_id(&cand);

        let conflict = claims.iter().any(|cl| {
            if fs >= cl.fe || cl.fs >= fe { return false; }  // no footprint overlap
            // Footprints overlap.  Allow if same ClaimId AND disjoint data.
            if cl.cid == cid && (de <= cl.ds || cl.de <= ds) { return false; }
            true
        });
        if conflict { continue; }
        chosen.push(cand);
        claims.push(Claim { fs, fe, ds, de, cid });
        if max_results > 0 && chosen.len() >= max_results { break; }
    }

    // Compound splitting — replace merged two-map layouts with their halves.
    let mut chosen = split_compound_tables(data, chosen);
    if max_results > 0 && chosen.len() > max_results {
        chosen.truncate(max_results);
    }

    chosen
}

// ---------------------------------------------------------------------------
// Compound table splitting
// ---------------------------------------------------------------------------

/// Detect and split compound tables — two maps sharing a Y axis stored as
/// ``[X1 axis][X2 axis][Y axis][row-interleaved data]``.
///
/// A table is split at column boundary *k* when the median row separates
/// (left half strictly above/below the right half) AND the boundary is a
/// value cliff (cross-boundary step ≥ 2× the median within-half step).
/// Real ECUs (Bosch EDC families) store such map pairs habitually; the
/// naive scanner reads them as one wide table (e.g. 16×8 instead of
/// 8×8 + 8×8).  Continuous single maps separate trivially but have no
/// cliff, so they stay intact.
///
/// The two halves keep the parent's score and carry the parent's row stride
/// (``Some(cols_total * cell_width)``) because their cells interleave per row.
fn split_compound_tables(data: &[u8], chosen: Vec<TableResult>) -> Vec<TableResult> {
    let mut out = Vec::with_capacity(chosen.len());
    for t in chosen {
        match try_split_compound(data, &t) {
            Some((left, right)) => {
                out.push(left);
                out.push(right);
            }
            None => out.push(t),
        }
    }
    out
}

fn try_split_compound(
    data: &[u8], t: &TableResult,
) -> Option<(TableResult, TableResult)> {
    let (off, cols, rows, cw, bo, x_off, y_off, score, stride) = t.clone();
    if stride.is_some() || rows < 3 || cols < 6 || (cw != 1 && cw != 2) {
        return None;
    }
    let le = bo == "little";

    // The compound layout `[X1][X2][Y][row-interleaved data]` has its data
    // immediately after the shared Y axis.  Only that pad is considered —
    // it makes the split deterministic and identical across stock/tuned
    // pairs (the scanner's own pad guess can drift).
    let preferred = (y_off? + rows * 2) as isize - off as isize;
    if !(-4..=4).contains(&preferred) { return None; }
    let base = (off as isize + preferred) as usize;
    if base + rows * cols * cw > data.len() { return None; }

    let mut grid: Vec<Vec<u32>> = Vec::with_capacity(rows);
    for r in 0..rows {
        grid.push(read_block(data, base + r * cols * cw, cols, le, cw)?);
    }

    // Candidate k must satisfy two gates across the MEDIAN row:
    //   1. separation — min(left) > max(right) (or vice versa); the median
    //      keeps this robust when a tune pushes one row into slight overlap.
    //   2. cliff — the cross-boundary step |right[0] - left[last]| must be
    //      at least twice the median within-half step.  A continuous single
    //      map (e.g. a ramp) separates trivially but has no cliff; a true
    //      compound pair has a value-regime boundary.
    let mut best: Option<(usize, f64)> = None;  // (k, cliff ratio)
    for k in 4..=(cols - 4) {
        let mut seps: Vec<i64> = Vec::with_capacity(rows);
        let mut crosses: Vec<i64> = Vec::with_capacity(rows);
        let mut within: Vec<i64> = Vec::with_capacity(rows * (cols - 1));
        for row in &grid {
            let (mut lmin, mut lmax) = (u32::MAX, 0u32);
            let (mut rmin, mut rmax) = (u32::MAX, 0u32);
            for &v in &row[..k] {
                if v < lmin { lmin = v; }
                if v > lmax { lmax = v; }
            }
            for &v in &row[k..] {
                if v < rmin { rmin = v; }
                if v > rmax { rmax = v; }
            }
            let sl = lmin as i64 - rmax as i64;
            let sr = rmin as i64 - lmax as i64;
            // Positive when separated (either direction), negative otherwise.
            seps.push(if sl > 0 || sr > 0 { sl.max(sr) } else { sl.max(sr) });
            crosses.push((row[k] as i64 - row[k - 1] as i64).abs());
            for w in row.windows(2) {
                within.push((w[1] as i64 - w[0] as i64).abs());
            }
        }
        seps.sort_unstable();
        crosses.sort_unstable();
        within.sort_unstable();
        let med_sep = seps[seps.len() / 2];
        let med_cross = crosses[crosses.len() / 2];
        let med_within = within[within.len() / 2].max(1);
        if med_sep <= 0 { continue; }
        if med_cross <= 2 * med_within { continue; }
        let ratio = med_cross as f64 / med_within as f64;
        if best.map_or(true, |(_, br)| ratio > br) {
            best = Some((k, ratio));
        }
    }

    let (k, _ratio) = best?;
    let row_stride = cols * cw;
    let left = (
        base, k, rows, cw, bo.clone(), x_off, y_off, score,
        Some(row_stride),
    );
    let right = (
        base + k * cw, cols - k, rows, cw, bo, x_off + k * 2, y_off,
        score, Some(row_stride),
    );
    Some((left, right))
}
