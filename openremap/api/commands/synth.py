"""
openremap.api.commands.synth — synthetic dump generation (the gate).

The generator itself is pure and lives in ``openremap.core.synth``; this module
only decodes parameters, calls it, and serialises the result.  The bytes come
back base64-encoded, like every other binary payload in the API.

Why an API method at all: a host (GUI/script/automation) can then create its
own fixtures without shipping anyone's ROM data — the generated file is built
from scratch and carries no OEM content.
"""

from __future__ import annotations

from openremap.api.errors import InvalidParamsError
from openremap.api.registry import register
from openremap.core.synth import generate_dump as _generate_dump
from openremap.core.synth.generator import PlantedMap

DEFAULT_LAYOUT = ("code", "calibration", "erased", "code")


def _map_payload(m: PlantedMap) -> dict:
    """Ground truth for one planted table.

    ``cells`` is deliberately omitted — it is large, trivially recomputed from
    the binary, and would bloat every RPC reply.
    """
    return {
        "map_id": m.map_id,
        "offset": m.offset,
        "end": m.end,
        "cols": m.cols,
        "rows": m.rows,
        "cell_width": m.cell_width,
        "byte_order": m.byte_order,
        "x_axis_offset": m.x_axis_offset,
        "y_axis_offset": m.y_axis_offset,
        "x_axis": list(m.x_axis),
        "y_axis": list(m.y_axis),
    }


@register(
    "generate_dump",
    kind="command",
    description=(
        "Generate a deterministic synthetic ECU-like dump from scratch (no OEM "
        "data).  Returns the bytes as base64 plus the planted ground truth. "
        "Generated files have no manufacturer fingerprints, so identify reports "
        "Unknown for them — by design."
    ),
    input_schema={
        "size": {"type": "int", "default": 262144, "min": 4096},
        "maps": {"type": "int", "default": 1, "min": 0, "max": 64},
        "seed": {"type": "int", "default": 7},
        "cell_width": {"type": "int", "default": 2, "min": 1, "max": 2},
        "byte_order": {"type": "str", "default": "little"},
        "layout": {"type": "list", "default": list(DEFAULT_LAYOUT)},
    },
)
def generate_dump_cmd(params: dict) -> dict:
    """Generate a dump and return ``data_b64`` + ground truth."""
    # Only an *absent* layout falls back to the default: an explicitly empty
    # list is a caller error, not a silent default.
    layout = params.get("layout")
    try:
        dump = _generate_dump(
            size=int(params.get("size", 262144)),
            maps=int(params.get("maps", 1)),
            seed=int(params.get("seed", 7)),
            layout=tuple(layout) if layout is not None else DEFAULT_LAYOUT,
            cell_width=int(params.get("cell_width", 2)),
            byte_order=str(params.get("byte_order", "little")),
        )
    except ValueError as exc:
        # Bad combination of parameters (unsupported layout, maps that do not
        # fit) — that is a caller error, not an internal failure.
        raise InvalidParamsError(f"generate_dump: {exc}") from exc

    return {
        "data_b64": dump.data,  # bytes → base64 at dispatch
        "sha256": dump.sha256,
        "size": len(dump.data),
        "sectors": [
            {"kind": s.kind, "start": s.start, "end": s.end} for s in dump.sectors
        ],
        "maps": [_map_payload(m) for m in dump.maps],
        "params": dump.params,
    }
