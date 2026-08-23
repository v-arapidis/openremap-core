//! Endianness detection — 1:1 port of the Python
//! `identifier._detect_endian` high-byte-zero heuristic (itself a port of
//! the studio's `analysis/endian.rs`).
//!
//! ECU calibration binaries are dominated by small unsigned integers.
//! Under the correct byte order the high byte(s) of each word are almost
//! always `0x00`; under the wrong order the zeros migrate to the low
//! byte.  Count high-byte-zero hits for both candidate orders; the larger
//! count wins.  All-zero words are skipped (symmetric), and the sample is
//! capped at 256 KiB.

use pyo3::prelude::*;

/// Maximum bytes sampled — same cap as the Python implementation.
const MAX_SAMPLE_BYTES: usize = 256 * 1024;

/// Minimum non-trivial words required before committing to a verdict.
const MIN_SAMPLES: usize = 32;

#[pyfunction]
pub fn detect_endian(data: &[u8], cell_bytes: usize) -> String {
    if cell_bytes < 2 {
        return "little".to_string();
    }

    let word_size = cell_bytes;
    let sample = &data[..MAX_SAMPLE_BYTES.min(data.len())];

    let mut le_high_zeros: usize = 0;
    let mut be_high_zeros: usize = 0;
    let mut samples: usize = 0;

    let mut i = 0;
    while i + word_size <= sample.len() {
        let word = &sample[i..i + word_size];
        if word.iter().all(|&b| b == 0) {
            i += word_size;
            continue;
        }
        // LE: high byte is at offset word_size - 1
        if word[word_size - 1] == 0 {
            le_high_zeros += 1;
        }
        // BE: high byte is at offset 0
        if word[0] == 0 {
            be_high_zeros += 1;
        }
        samples += 1;
        i += word_size;
    }

    if samples < MIN_SAMPLES {
        return "little".to_string();
    }

    if le_high_zeros >= be_high_zeros {
        "little".to_string()
    } else {
        "big".to_string()
    }
}
