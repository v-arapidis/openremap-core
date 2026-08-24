"""
Shared test helpers and fixtures for the tuning pipeline test suite.

All helpers are plain functions (not pytest fixtures) so tests can
compose them freely without fixture dependency injection overhead.
"""

import math
import random
import struct


# ---------------------------------------------------------------------------
# Binary helpers
# ---------------------------------------------------------------------------


def make_bin(size: int = 1024, fill: int = 0x00) -> bytes:
    """Return a bytes object of `size` bytes all set to `fill`."""
    return bytes([fill] * size)


def make_bin_with(size: int, patches: dict) -> bytes:
    """
    Build a zero-filled binary of `size` bytes and write specific values.

    Args:
        size:    Total size in bytes.
        patches: Dict of {offset: value_bytes} where value_bytes is bytes or int.
                 An int value is written as a single byte.

    Example:
        make_bin_with(1024, {100: b"\\xAA\\xBB", 200: 0xFF})
    """
    buf = bytearray(size)
    for offset, value in patches.items():
        if isinstance(value, int):
            buf[offset] = value
        else:
            buf[offset : offset + len(value)] = value
    return bytes(buf)


# ---------------------------------------------------------------------------
# Flash-layout fixture — distinct sectors the segmenter can label
# ---------------------------------------------------------------------------

_LAYOUT_X = [300, 600, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 5500, 6000, 6500, 7000]
_LAYOUT_Y = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def _layout_surface(xi: int, yi: int) -> int:
    return (
        600
        + yi * 100
        + int(900 * math.sin(xi / 2.1))
        + 40 * ((xi * 7 + yi * 13) % 5)
    )


def make_layout_bin(seed: int = 7, map_delta: int = 0) -> bytes:
    """
    Build a deterministic 256 KB binary with a clear flash layout.

    Four 64 KB sectors the segmenter labels distinctly:
        sector 0 (0x000000-0x010000)  random      -> code
        sector 1 (0x010000-0x020000)  random fill + one real map at
                                      0x11000     -> calibration
        sector 2 (0x020000-0x030000)  zeros       -> erased
        sector 3 (0x030000-0x040000)  random      -> code (may hold
                                      low-score junk tables)

    ``map_delta`` shifts every cell of the real map (a "tune"), so a
    stock/tuned pair is ``make_layout_bin(seed, 0)`` / ``make_layout_bin(
    seed, delta)`` — everything else stays byte-identical.
    """
    buf = bytearray(256 * 1024)
    rng = random.Random(seed)
    buf[0x00000:0x10000] = rng.randbytes(0x10000)
    buf[0x10000:0x20000] = rng.randbytes(0x10000)
    buf[0x30000:0x40000] = rng.randbytes(0x10000)

    off = 0x11000
    buf[off : off + 2 * len(_LAYOUT_X)] = struct.pack(
        f"<{len(_LAYOUT_X)}H", *_LAYOUT_X
    )
    off += 2 * len(_LAYOUT_X)
    buf[off : off + 2 * len(_LAYOUT_Y)] = struct.pack(
        f"<{len(_LAYOUT_Y)}H", *_LAYOUT_Y
    )
    off += 2 * len(_LAYOUT_Y)
    for yi in range(len(_LAYOUT_Y)):
        for xi in range(len(_LAYOUT_X)):
            struct.pack_into(
                "<H", buf, off,
                max(0, min(65535, _layout_surface(xi, yi) + map_delta)),
            )
            off += 2
    return bytes(buf)


# ---------------------------------------------------------------------------
# Recipe helpers
# ---------------------------------------------------------------------------


def make_recipe(instructions: list, ecu: dict | None = None) -> dict:
    """
    Build a minimal format-4.3 recipe dict.

    Args:
        instructions: List of instruction dicts (use make_instruction()).
        ecu:          Optional ecu block. Defaults to an empty dict.
    """
    return {
        "type": "recipe",
        "schema_version": "4.3",
        "source": "tune_export",
        "application": "openremap-studio",
        "metadata": {},
        "ecu": ecu or {},
        "statistics": {},
        "instructions": instructions,
    }


def make_instruction(
    offset: int,
    ob: str,
    mb: str,
    ctx: str = "",
) -> dict:
    """
    Build a single recipe instruction dict.

    Args:
        offset: Absolute byte offset in the binary.
        ob:     Original bytes as uppercase hex string (e.g. "AABB").
        mb:     Modified bytes as uppercase hex string (e.g. "CCDD").
        ctx:    Context-before bytes as uppercase hex string. Default empty.
    """
    ob = ob.upper()
    mb = mb.upper()
    ctx = ctx.upper()
    return {
        "offset": offset,
        "offset_hex": f"{offset:X}",
        "size": len(bytes.fromhex(ob)),
        "ob": ob,
        "mb": mb,
        "ctx": ctx,
        "context_after": "",
        "context_size": len(bytes.fromhex(ctx)) if ctx else 0,
        "description": f"{len(bytes.fromhex(ob))} bytes at 0x{offset:X} modified",
    }


def ctx_hex(data: bytes, offset: int, size: int = 8) -> str:
    """
    Extract `size` bytes immediately before `offset` from `data` as uppercase hex.
    Returns an empty string if offset is 0 or data is too short.
    """
    start = max(0, offset - size)
    return data[start:offset].hex().upper()
