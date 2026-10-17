"""
The known-family fixtures the wizard measures against.

**Where they come from.** The repository's own fixture recipes,
``_dev/contrib/recipes/*.json`` — the same files the mimic corpus is built from.
They are committed, so anybody with a checkout has them; there is deliberately
**no copy shipped inside the package**. An earlier version vendored a generated
``fixtures.json`` into the wheel so that `pip install` users would get collision
checking, and it was the wrong trade: it duplicated knowledge that already has
one home, and it made the wizard blind to a recipe it had *itself* just written
into the tree until somebody re-ran the export.

**What that means for a user without a checkout.** A `pip install` has no
recipes, so the wizard can still *measure* their dump (that is pure local
computation) but cannot check collisions and will not write artifacts — the
contribution path is a pull request, which requires a clone anyway. That split is
enforced in :mod:`~openremap.core.services.wizard.report` and
:mod:`~openremap.core.services.wizard.plan`, not papered over here.

**These are our assumptions, not evidence.** Every fixture was written by reading
our own extractors, so a collision means "this marker overlaps what we ourselves
use to recognise that family" — a strong warning, never proof about real ECUs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

RECIPES_SUBPATH = Path("_dev") / "contrib" / "recipes"


@dataclass(frozen=True)
class KnownFixture:
    """One file we can build, and the identity it is expected to produce."""

    name: str
    manufacturer: str
    family: str
    variant: str | None
    data: bytes
    spec: dict = None  # type: ignore[assignment]  # the recipe it was built from

    @property
    def family_key(self) -> str:
        """The label a contributor would recognise: the variant when there is one.

        Bosch EDC16 reports ``ecu_family="EDC16"`` for ten different C-numbers
        and carries the distinction in the variant, so reporting the family
        alone would tell a contributor "this collides with EDC16" when the file
        is specifically an EDC16C34.
        """
        return self.variant or self.family


def find_recipes_dir(start: Path | None = None) -> Path | None:
    """Locate ``_dev/contrib/recipes`` — from the cwd upward, then the package.

    The cwd is searched first so that a contributor running the wizard from
    anywhere inside their clone gets *their* recipes (including one the wizard
    just wrote), and the package location second so an installed copy inside a
    clone still finds the checkout. Neither is a network or config lookup: no
    recipes means no collision checking, and the caller says so.
    """
    starts = [start] if start is not None else [Path.cwd(), Path(__file__).resolve().parent]
    for origin in starts:
        for candidate in [origin, *origin.parents]:
            recipes = candidate / RECIPES_SUBPATH
            if recipes.is_dir():
                return recipes
    return None


def _recipe_to_spec(path: Path) -> dict:
    """Adapt a dev-side recipe to the shape the wizard builds fixtures from.

    The dev recipes carry an ``expect`` block (manufacturer + family, plus a
    variant when the family distinction lives there); the wizard wants those
    flattened onto the spec. Hand-written recipes may omit the variant, so
    nothing is assumed present.
    """
    recipe = json.loads(path.read_text(encoding="utf-8"))
    expect = recipe.get("expect", {})
    return {
        "name": path.name.replace(".json", "") + f"-{int(recipe['file_size']) // 1024}k.mimic",
        "recipe": path.name,
        "manufacturer": expect.get("manufacturer") or recipe.get("manufacturer"),
        "family": expect.get("ecu_family"),
        "variant": expect.get("ecu_variant"),
        "file_size": int(recipe["file_size"]),
        "fill": recipe.get("fill", "FF"),
        "plants": recipe.get("plants", []),
    }


def _position(expr: str, size: int) -> int:
    """Resolve a plant position: ``0x7FD9`` or ``end-0x40``."""
    expr = expr.strip()
    if expr.startswith("end-"):
        return size - int(expr[4:], 16)
    return int(expr, 16)


def _token_bytes(token: dict) -> bytes:
    if "text" in token:
        return token["text"].encode("ascii")
    if "hex" in token:
        return bytes.fromhex(token["hex"].replace(" ", ""))
    if "run" in token:
        return bytes([int(token["run"], 16)]) * int(token["count"])
    raise ValueError(f"unknown recipe token: {token!r}")


def build_fixture(spec: dict) -> bytes:
    """Rebuild one fixture from its recipe (fill byte + planted payloads).

    Deliberately the same algorithm as ``_dev/contrib/build_corpus.py``: the
    fixtures are only trustworthy if they rebuild the *same bytes* as the
    committed corpus, and a test asserts exactly that against the manifest
    hashes.
    """
    size = int(spec["file_size"])
    fill = bytes.fromhex(spec.get("fill", "FF"))
    if len(fill) != 1:
        raise ValueError(f"{spec.get('name')}: fill must be exactly one byte")
    buf = bytearray(fill * size)

    for plant in spec.get("plants", []):
        pos = _position(plant["at"], size)
        for token in plant["seq"]:
            payload = _token_bytes(token)
            if pos + len(payload) > size:
                raise ValueError(
                    f"{spec.get('name')}: plant at {plant['at']} runs past EOF"
                )
            buf[pos : pos + len(payload)] = payload
            pos += len(payload)

    return bytes(buf)


@lru_cache(maxsize=1)
def fixture_specs() -> tuple[dict, ...]:
    """The recipes of the checkout we are running inside, in name order.

    Cached per process: a wizard run is short-lived, and the only writer (the
    ``--write`` half) touches the tree at the very end. Cross-run staleness — the
    bug an earlier version had — cannot happen here, because there is no second
    copy to fall out of date. ``reset_cache()`` exists for tests.
    """
    recipes_dir = find_recipes_dir()
    if recipes_dir is None:
        return ()
    return tuple(_recipe_to_spec(path) for path in sorted(recipes_dir.glob("*.json")))


def reset_cache() -> None:
    """Forget the loaded recipes (tests, and a long-lived host process)."""
    fixture_specs.cache_clear()
    known_fixtures.cache_clear()


def fixture_count() -> int:
    return len(fixture_specs())


def fixtures_available() -> bool:
    """True when collision checking is possible at all."""
    return bool(fixture_specs())


@lru_cache(maxsize=1)
def known_fixtures() -> tuple[KnownFixture, ...]:
    """Every known-family fixture, rebuilt. Empty without a checkout."""
    return tuple(
        KnownFixture(
            name=spec["name"],
            manufacturer=spec["manufacturer"],
            family=spec["family"],
            variant=spec.get("variant"),
            data=build_fixture(spec),
            spec=spec,
        )
        for spec in fixture_specs()
    )
