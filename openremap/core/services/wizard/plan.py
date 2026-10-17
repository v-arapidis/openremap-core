"""
Turn a measurement into a plan: the spec, the artifacts, and the proof.

The seam between measuring and rendering. It decides the three things the report
leaves open — which marker to use, which sizes to gate on, and which exclusion
guards to write — then renders, then **runs** what it rendered
(:mod:`~openremap.core.services.wizard.verify`) before returning anything.

Exclusion guards are harvested, not invented. When a marker collides with a
known family, the guard against that family is its own family token, read from
the fixture's recipe (`EDC16C34` collides → guard on `EDC16C34`): the wizard
knows that string because it built the fixture that contains it. A guard that
does not actually work is caught by the verification pass, which is why the
guard is written before the skeleton is executed rather than trusted.
"""

from __future__ import annotations

from openremap.core.services.wizard.collisions import marker_offsets
from openremap.core.services.wizard.emit import (
    Spec,
    render_extractor,
    render_fixture,
    render_report,
    render_test,
)
from openremap.core.services.wizard.fixtures import (
    KnownFixture,
    fixtures_available,
    known_fixtures,
)
from openremap.core.services.wizard.report import analyze
from openremap.core.services.wizard.verify import verify_generated

# Repository destinations for the generated artifacts.
RECIPE_DIR = "_dev/contrib/recipes"
MANUFACTURER_DIR = "openremap/core/manufacturers"
TEST_DIR = "tests/tuning/manufacturers"


def _fixture_payloads(spec: dict) -> list[bytes]:
    """Every planted payload of a fixture recipe, as bytes."""
    out: list[bytes] = []
    for plant in spec.get("plants", []):
        payload = bytearray()
        for token in plant["seq"]:
            if "text" in token:
                payload += token["text"].encode("ascii")
            elif "hex" in token:
                payload += bytes.fromhex(token["hex"].replace(" ", ""))
            elif "run" in token:
                payload += bytes([int(token["run"], 16)]) * int(token["count"])
        out.append(bytes(payload))
    return out


def guard_for(fixture: KnownFixture) -> tuple[bytes, str] | None:
    """The byte-string that identifies *fixture*'s family, for an exclusion guard.

    Prefers the fixture's family key (``EDC16C34``) and falls back to the family
    (``EDC16``) — the broader token rejects more siblings, which is what a guard
    is for. None when the fixture's own recipe does not spell its name, in which
    case no guard can be derived and the wizard says so instead of guessing.
    """
    for candidate in (fixture.family_key, fixture.family):
        if not candidate:
            continue
        marker = candidate.encode("ascii", "ignore")
        if len(marker) < 4:
            continue
        if any(marker in payload for payload in _fixture_payloads(fixture.spec or {})):
            return marker, f"{fixture.manufacturer} {fixture.family_key}"
    return None


def build_spec(
    analysis: dict,
    *,
    brand: str | None = None,
    family: str | None = None,
    marker: bytes | None = None,
    fixtures: tuple[KnownFixture, ...] | None = None,
) -> Spec:
    """Assemble the render spec from a measured report."""
    pool = fixtures if fixtures is not None else known_fixtures()
    dumps = analysis.get("dumps", [])

    chosen = marker
    offsets: dict[str, int] = {}
    collides: list[str] = []
    if chosen is None:
        recommended = analysis.get("recommended")
        if not recommended:
            raise ValueError(
                "no collision-free marker was found in this dump set: pass one "
                "explicitly with --marker, or read the report's candidates and "
                "pick a window of one"
            )
        chosen = recommended.encode("latin-1")
        for candidate in analysis.get("candidates", []):
            if candidate["marker"] == recommended:
                offsets = dict(candidate.get("offsets", {}))
                collides = list(candidate.get("collision_families", []))
                break

    if not offsets:
        # An explicit marker still needs its offsets, and they must come from the
        # dumps — a marker the contributor chose but that is absent from their own
        # dump would generate a fixture their extractor cannot accept.
        for name, data in analysis.get("_raw_dumps", []) or []:
            found = marker_offsets(data, chosen, limit=1)
            if found:
                offsets[name] = found[0]

    if not offsets:
        # Refused, not silently repaired: planting a marker that no provided dump
        # contains would produce a fixture and a passing skeleton for a signature
        # the contributor's family does not actually carry — the one failure mode
        # this tool exists to prevent.
        raise ValueError(
            f"marker {chosen.decode('latin-1')!r} does not occur in any of the "
            "provided dumps, so it cannot be this family's signature"
        )

    if not collides:
        from openremap.core.services.wizard.collisions import marker_collisions

        collides = sorted({c.family for c in marker_collisions(chosen, pool)})

    exclusions: list[tuple[bytes, str]] = []
    for fixture in pool:
        if fixture.family_key in collides or fixture.family in collides:
            guard = guard_for(fixture)
            if guard and guard not in exclusions:
                exclusions.append(guard)

    sizes = sorted({int(dump["size"]) for dump in dumps})
    fields = _field_hints(analysis.get("fields", []))

    return Spec(
        brand=brand,
        family=family,
        marker=chosen,
        sizes=sizes,
        exclusions=exclusions,
        fields=fields,
        marker_offset=next(iter(offsets.values()), None),
        collides_with=sorted(set(collides)),
        notes=analysis.get("notes", []),
    )


def _field_hints(fields: list[dict]) -> dict:
    """Turn measured fields into TODOs and comments for the skeleton."""
    hints: dict = {}
    stable = [f for f in fields if f.get("stable") and f.get("value")]
    varying = [f for f in fields if not f.get("stable")]
    if stable:
        hints["stable"] = {f["kind"]: f["value"] for f in stable}
    if varying:
        hints["software_version_hint"] = ", ".join(
            f"{f['kind']} ({', '.join(sorted(set(f['values'].values())))})"
            for f in varying
            if f["kind"] in ("bosch_sw", "sw_12digit")
        )
    return hints


def prepare(
    dumps: list[tuple[str, bytes]],
    *,
    brand: str | None = None,
    family: str | None = None,
    marker: bytes | None = None,
    fixtures: tuple[KnownFixture, ...] | None = None,
    measure_only: bool = False,
) -> dict:
    """Measure, render, and verify — the whole pass, in one call.

    Two modes, and the strict one is the default:

    * **contribute** (default) — needs the repository's fixture recipes. Without
      them the marker cannot be checked against other families, and generating an
      extractor from an unchecked marker is how a contribution steals binaries
      from a family that already worked. Refuses rather than degrading.
    * **measure** (*measure_only=True*) — the half that is pure local
      computation: who claims the dump, the runs stable across the dumps, the
      candidate identity fields. No collision check, no recommendation, no
      artifacts, and the report says all three. This is what a `pip install`
      without a checkout can still do, and it takes an explicit opt-in so nobody
      mistakes an unchecked ranking for a contribution-ready answer.
    """
    full_pool = fixtures if fixtures is not None else known_fixtures()
    if fixtures is None and not fixtures_available():
        if not measure_only:
            raise ValueError(
                "no fixture recipes found (_dev/contrib/recipes/): the collision "
                "check and artifact generation need a checkout of openremap-core. "
                "Re-run with measure_only (CLI: --measure-only) to get the "
                "measurement without the collision check."
            )
        analysis = analyze(
            dumps, fixtures=(), exclude_families=(family,) if family else ()
        )
        return {
            "mode": "measure",
            "spec": None,
            "analysis": analysis,
            "artifacts": None,
            "verification": None,
            "paths": {},
        }
    if not brand or not family:
        raise ValueError(
            "brand and family are required to generate artifacts (they name the "
            "family the extractor declares); measure_only does not need them"
        )

    # The report does the excluding, so it can *say* what it excluded; the rest of
    # the pass then works from the filtered pool. Guard harvesting in particular
    # must not see this family's own fixture: a guard derived from it would reject
    # the contributor's own files.
    analysis = analyze(dumps, fixtures=full_pool, exclude_families=(family,))
    analysis["_raw_dumps"] = [(name, data) for name, data in dumps]
    pool = tuple(
        fixture
        for fixture in full_pool
        if family is None
        or (fixture.family != family and fixture.family_key != family)
    )

    spec = build_spec(analysis, brand=brand, family=family, marker=marker, fixtures=pool)

    own_fixture = _build_own_fixture(spec, dumps)
    source = render_extractor(spec)
    verification = verify_generated(
        source, family=family, own_fixture=own_fixture, fixtures=pool
    )

    recipe = render_fixture(spec, file_size=len(own_fixture))
    artifacts = {
        "recipe": recipe,
        "extractor": source,
        "test": render_test(spec, spec.fixture_name),
        "report": render_report(spec, analysis, verification),
    }
    package, module = spec.package_parts
    paths = {
        # The canonical names, so no consumer re-derives them: the CLI printed
        # `continental.sim2k-241.extractor` from the family string, which is not
        # even valid Python.
        "package": f"{MANUFACTURER_DIR}/{package}/{module}",
        "recipe": f"{RECIPE_DIR}/{spec.fixture_name}",
        "extractor": f"{MANUFACTURER_DIR}/{package}/{module}/extractor.py",
        "test": f"{TEST_DIR}/test_{module}_extractor.py",
        "report": f"{spec.family.lower()}-contribution.md",
    }

    analysis.pop("_raw_dumps", None)
    return {
        "mode": "contribute",
        "spec": {
            "brand": spec.brand,
            "family": spec.family,
            "marker": spec.marker.decode("latin-1"),
            "marker_hex": spec.marker.hex(),
            "marker_offset": spec.marker_offset,
            "sizes": spec.sizes,
            "exclusions": [
                {"marker": marker.decode("latin-1"), "why": why}
                for marker, why in spec.exclusions
            ],
            "collides_with": spec.collides_with,
        },
        "analysis": analysis,
        "artifacts": artifacts,
        "verification": verification,
        "paths": paths,
    }


def _build_own_fixture(spec: Spec, dumps: list[tuple[str, bytes]]) -> bytes:
    """The fixture bytes the skeleton must accept — built the corpus way.

    Size comes from the contributor's own dump (that is the family's known size),
    fill from the corpus convention, payload is the marker at its measured
    offset. Building it here and *not* only rendering the recipe is what lets the
    verification pass run before anything is written to disk.
    """
    from openremap.core.services.wizard.fixtures import build_fixture

    size = max((len(data) for _name, data in dumps), default=0)
    return build_fixture(
        {
            "name": spec.fixture_name,
            "file_size": size,
            "fill": "FF",
            "plants": [
                {
                    "at": f"0x{(spec.marker_offset or 0):X}",
                    "seq": [{"hex": spec.marker.hex()}],
                }
            ],
        }
    )
