"""
openremap.api.commands.contribute — the contribution wizard's gate.

Two methods, both thin: path I/O here, everything else in
``openremap.core.services.wizard``. No business logic lives in this module — it
loads bytes, calls the service, and returns what the service produced.

* ``contribute_extractor`` — measure a contributor's own dumps and return the
  report plus the rendered artifacts (fixture recipe, extractor source, test
  source, PR report) and the mechanical verification of the skeleton.
* ``contribute_check`` — run the same review bar over an existing extractor file,
  which is what a reviewer wants before merging a contribution.

Nothing here writes files: the CLI decides where artifacts go, and a host can
diff them first. That keeps the "never commit a ROM" rule enforceable at the one
place that touches the filesystem.
"""

from __future__ import annotations

from pathlib import Path

from openremap.api._io import load_image
from openremap.api.errors import InvalidParamsError
from openremap.api.registry import register
from openremap.core.services.wizard import check_file, prepare


@register(
    "contribute_extractor",
    kind="command",
    description=(
        "Measure one or more of your own ECU dumps for a new family and render "
        "the artifacts a pull request needs: a committable fixture recipe, an "
        "extractor skeleton, a test, and a report. Runs the rendered skeleton "
        "against every known-family fixture and refuses to call it verified if "
        "it steals one."
    ),
    input_schema={
        "paths": {
            "type": "list",
            "required": True,
            "description": "Your dump(s); all must be the same family.",
        },
        "brand": {
            "type": "str",
            "description": "Manufacturer name, e.g. Siemens. Required unless "
            "measure_only: measurement does not need to know the answer.",
        },
        "family": {
            "type": "str",
            "description": "Family key, e.g. SID206. Required unless measure_only.",
        },
        # Optional, and deliberately without a `default`: the registry validates
        # declared defaults against their type, so `default: None` on a str param
        # is rejected before the command ever runs. Absent means absent.
        "marker": {
            "type": "str",
            "description": "Override the chosen signature (text); default: the "
            "top collision-free candidate.",
        },
        "marker_hex": {
            "type": "str",
            "description": "Override the chosen signature (hex), for non-ASCII bytes.",
        },
        "measure_only": {
            "type": "bool",
            "default": False,
            "description": "Measure without the collision check (no checkout needed). "
            "No marker is recommended and no artifacts are produced.",
        },
    },
)
def contribute_extractor(params: dict) -> dict:
    paths = params.get("paths") or []
    if not paths:
        raise InvalidParamsError("contribute_extractor: at least one path is required")

    dumps: list[tuple[str, bytes]] = []
    for raw in paths:
        path = Path(str(raw))
        try:
            data, _container = load_image(str(path))
        except FileNotFoundError as exc:
            raise InvalidParamsError(f"contribute_extractor: {exc}") from exc
        dumps.append((path.name, data))

    marker = _marker_from(params)
    measure_only = bool(params.get("measure_only", False))
    brand, family = params.get("brand"), params.get("family")
    if not measure_only and (not brand or not family):
        raise InvalidParamsError(
            "contribute_extractor: brand and family are required to generate a "
            "contribution; pass measure_only for a measurement that needs neither"
        )
    try:
        return prepare(
            dumps,
            brand=str(brand) if brand else None,
            family=str(family) if family else None,
            marker=marker,
            measure_only=measure_only,
        )
    except ValueError as exc:
        # Bad identifiers, an unusable marker, or no collision-free candidate:
        # all caller-side, all actionable, none of them internal failures.
        raise InvalidParamsError(f"contribute_extractor: {exc}") from exc


@register(
    "contribute_check",
    kind="command",
    description=(
        "Run the contribution review bar over an existing extractor source file: "
        "does it still claim its own fixture, does it steal another family's, "
        "does it clear evidence on a miss, does it declare what it reports."
    ),
    input_schema={
        "path": {
            "type": "str",
            "required": True,
            "description": "Path to the extractor module (…/extractor.py).",
        },
    },
)
def contribute_check(params: dict) -> dict:
    path = Path(str(params["path"]))
    if not path.is_file():
        raise InvalidParamsError(f"contribute_check: no such file: {path}")
    return check_file(path)


def _marker_from(params: dict) -> bytes | None:
    """The optional explicit marker, from text or hex."""
    text = params.get("marker")
    hexed = params.get("marker_hex")
    if text and hexed:
        raise InvalidParamsError("contribute_extractor: pass marker or marker_hex, not both")
    if hexed:
        try:
            return bytes.fromhex(str(hexed).replace(" ", ""))
        except ValueError as exc:
            raise InvalidParamsError(f"contribute_extractor: bad marker_hex: {exc}") from exc
    if text:
        return str(text).encode("latin-1", "replace")
    return None
