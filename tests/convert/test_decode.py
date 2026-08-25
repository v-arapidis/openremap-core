"""
Unit tests for ``openremap.core.services.convert`` — Intel HEX / S-Record /
raw-binary image decoding.

Pure bytes-in/bytes-out — no I/O.  Fixtures are generated through the
module's own ``encode_ihex`` / ``encode_srec`` (which double as the public
export API), so every test round-trips a known byte array.
"""

import pytest

from openremap.core.services.convert import (
    MAX_IMAGE_SPAN,
    DecodeResult,
    decode_image,
    encode_ihex,
    encode_srec,
)

# ---------------------------------------------------------------------------
# Sniffing
# ---------------------------------------------------------------------------


def test_raw_binary_passthrough():
    """Non-HEX/SREC input is returned byte-identical, format 'binary'."""
    raw = b"\x00\x01\x02\xff\xfe EDC17C66" + b"\x00" * 64
    result = decode_image(raw)
    assert result.format == "binary"
    assert result.data == raw
    assert result.warnings == []
    assert result.segments == 0


def test_whitespace_prefixed_hex_is_sniffed():
    raw = b"  \t\n" + encode_ihex(b"\x11\x22").encode()
    result = decode_image(raw)
    assert result.format == "ihex"
    assert result.data == b"\x11\x22"


def test_lone_s_is_raw():
    """A single 'S' byte is not an S-Record (needs a type digit next)."""
    result = decode_image(b"S")
    assert result.format == "binary"


def test_s_record_sniffed():
    srec = encode_srec(b"\x11\x22\x33\x44")
    result = decode_image(srec.encode())
    assert result.format == "srec"
    assert result.data == b"\x11\x22\x33\x44"


def test_empty_input_is_binary():
    result = decode_image(b"")
    assert result.format == "binary"
    assert result.data == b""


# ---------------------------------------------------------------------------
# Round-trips
# ---------------------------------------------------------------------------


def test_ihex_round_trip():
    data = bytes(range(256)) * 4
    assert decode_image(encode_ihex(data).encode()).data == data


def test_srec_round_trip():
    data = b"\xde\xad\xbe\xef" * 100
    assert decode_image(encode_srec(data).encode()).data == data


def test_nonzero_base_normalised():
    """A file based at 0x80000000 decodes to a flat image starting at 0."""
    data = b"\x11\x22\x33\x44"
    result = decode_image(encode_ihex(data, base=0x80000000).encode())
    assert result.format == "ihex"
    assert result.data == data
    assert result.address_min == 0x80000000
    assert result.address_max == 0x80000004


# ---------------------------------------------------------------------------
# Addresses & gaps
# ---------------------------------------------------------------------------


def test_gaps_filled_with_0xff_and_warned():
    """Non-contiguous segments are joined with 0xFF and reported."""
    ihex = encode_ihex(b"\xaa" * 4, base=0x100) + "\n" + encode_ihex(
        b"\xbb" * 4, base=0x110
    )
    result = decode_image(ihex.encode())
    assert result.format == "ihex"
    assert result.segments == 2
    assert len(result.data) == 20  # 4 + 12 gap + 4
    assert result.data[:4] == b"\xaa" * 4
    assert result.data[4:16] == b"\xff" * 12
    assert result.data[16:] == b"\xbb" * 4
    assert any("gaps" in w for w in result.warnings)


def test_single_segment_no_warning():
    result = decode_image(encode_ihex(b"\x11" * 8).encode())
    assert result.segments == 1
    assert result.warnings == []


def test_span_beyond_limit_refused():
    """Data spread across >256 MB must be refused before materialising."""
    ihex = (
        encode_ihex(b"\xaa", base=0)
        + "\n"
        + encode_ihex(b"\xbb", base=MAX_IMAGE_SPAN + 1)
    )
    with pytest.raises(ValueError, match="sanity limit"):
        decode_image(ihex.encode())


# ---------------------------------------------------------------------------
# Error handling & the raw fallback
# ---------------------------------------------------------------------------


def test_corrupt_checksum_raises():
    """A record-shaped file with a bad checksum is a loud error."""
    lines = encode_ihex(b"\x11\x22\x33\x44").strip().splitlines()
    first = lines[0]
    chk = first[-2:]
    flipped = "00" if chk != "00" else "01"
    bad_file = "\n".join([first[:-2] + flipped] + lines[1:]) + "\n"
    with pytest.raises(ValueError, match="invalid ihex data"):
        decode_image(bad_file.encode())


def test_garbage_with_colon_prefix_falls_back_to_raw():
    """A raw dump that happens to start with ':' must stay raw (Subaru .hex!)."""
    raw = b":" + b"\x00" * 64
    result = decode_image(raw)
    assert result.format == "binary"
    assert result.data == raw
    assert any("treated as raw binary" in w for w in result.warnings)


def test_garbage_with_s_prefix_falls_back_to_raw():
    raw = b"S9" + b"\xff" * 32
    result = decode_image(raw)
    assert result.format == "binary"
    assert result.data == raw


def test_force_ihex_raises_on_garbage():
    """--format ihex must be strict: no raw fallback."""
    with pytest.raises(ValueError, match="invalid ihex data"):
        decode_image(b":" + b"\x00" * 64, force="ihex")


def test_force_srec_raises_on_garbage():
    with pytest.raises(ValueError, match="invalid srec data"):
        decode_image(b"S0" + b"\xff" * 32, force="srec")


def test_force_bin_skips_sniffing():
    """--format bin must treat even a valid HEX file as raw bytes."""
    ihex_bytes = encode_ihex(b"\x11").encode()
    result = decode_image(ihex_bytes, force="bin")
    assert result.format == "binary"
    assert result.data == ihex_bytes


def test_header_only_file_has_no_data():
    """A file with only the EOF record decodes to an error."""
    with pytest.raises(ValueError, match="no data found"):
        decode_image(b":00000001FF\n")
