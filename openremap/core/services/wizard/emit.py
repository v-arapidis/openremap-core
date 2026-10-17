"""
Render the artifacts a pull request needs, from the measured report.

Four outputs, and the reason each exists:

* **fixture recipe** (`<brand>-<family>.json`) — the contributor's family as
  fill byte + planted payloads, a few hundred bytes instead of a 1 MB ROM. This
  is the only way their ECU ever enters the repository: binaries are gitignored
  on purpose, so a contribution that needs the dump attached cannot be merged.
* **extractor source** — a skeleton that already satisfies the repository's
  review bar (size gate, exclusion guards, evidence tags on every `return False`,
  `match_key` built from what was actually extracted), so the contributor edits
  a working file instead of assembling one from a guide.
* **test source** — the fixture-driven test a reviewer will ask for anyway.
* **report** — the filled-in PR description: what was measured, which marker was
  chosen, what it collides with, and what was *not* verified.

Everything is rendered from a spec dict (`brand`, `family`, `marker`, `sizes`,
`exclusions`, `fields`, `notes`), so the same spec could be rendered as
declarative data later without re-deriving anything — which is the only reason
the spec exists separately from these templates.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field

# Identifiers become class and module names, so they are restricted to what can
# be spelled in both: a family called "SID206+Proto" must not produce invalid
# Python or a path that escapes the package.
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-+]{0,23}$")

FILL_BYTE = "FF"          # the corpus convention: erased flash, cheapest to diff
MIN_EXCLUSION_LEN = 4     # a guard byte-string shorter than this matches by luck


@dataclass
class Spec:
    """Everything the renderer needs; built from the measured report."""

    brand: str
    family: str
    marker: bytes
    sizes: list[int] = dc_field(default_factory=list)
    exclusions: list[tuple[bytes, str]] = dc_field(default_factory=list)
    fields: dict = dc_field(default_factory=dict)
    marker_offset: int | None = None
    collides_with: list[str] = dc_field(default_factory=list)
    notes: list[str] = dc_field(default_factory=list)

    def validate(self) -> None:
        for label, value in (("brand", self.brand), ("family", self.family)):
            if not _IDENT.match(value or ""):
                raise ValueError(
                    f"invalid {label} {value!r}: expected a name like 'Siemens' or "
                    "'SID206' (letters, digits, dot, dash, plus; max 24 chars)"
                )
        if not self.marker or len(self.marker) < 4:
            raise ValueError("marker: at least 4 bytes are needed to be distinctive")
        if any(len(m) < MIN_EXCLUSION_LEN for m, _ in self.exclusions):
            raise ValueError("exclusions: every guard needs at least 4 bytes")

    # -- derived names -----------------------------------------------------

    @property
    def class_name(self) -> str:
        return f"{_alnum(self.brand)}{_alnum(self.family)}Extractor"

    @property
    def fixture_name(self) -> str:
        return f"{self.brand.lower()}-{self.family.lower()}.json"

    @property
    def package_parts(self) -> tuple[str, str]:
        return _alnum(self.brand).lower(), _alnum(self.family).lower()

    @property
    def test_class_name(self) -> str:
        return f"Test{self.class_name}"


def _alnum(value: str) -> str:
    """Class-name-safe form: 'M1.55' → 'M155', 'SH72546' → 'SH72546'."""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", value or "")
    return cleaned or "Family"


def _bytes_literal(payload: bytes) -> str:
    """A Python byte-string literal that cannot break out of its quotes.

    Markers come from the contributor's own dump, so they can contain quotes,
    backslashes and newlines; ``repr`` of a bytes object escapes all of them and
    keeps the rendered file valid. Non-ASCII bytes stay escaped, which is what a
    reviewer wants to see next to an offset.
    """
    body = repr(payload)
    if body.startswith("b'") or body.startswith('b"'):
        return body
    return "b" + body


def _sizes_expr(sizes: list[int]) -> str:
    if not sizes:
        return "()  # TODO: the dump(s) gave no size — fill in the family's sizes"
    body = ", ".join(f"0x{size:X}" for size in sizes)
    # A one-element tuple needs its trailing comma: `(0x40000)` is an int, and
    # `len(data) in 262144` fails at runtime with a TypeError.
    return f"({body}{',' if len(sizes) == 1 else ''})"


def render_fixture(spec: Spec, *, file_size: int) -> dict:
    """The committable fixture recipe for this family."""
    spec.validate()
    plants = [
        {
            "at": f"0x{(spec.marker_offset or 0):X}",
            "seq": [{"hex": spec.marker.hex()}],
        }
    ]
    return {
        "manufacturer": spec.brand,
        "ecu_family": spec.family,
        "file_size": file_size,
        "fill": FILL_BYTE,
        "notes": (
            "Fixture for the contribution wizard's new family. Built from the "
            "contributor's measured marker only — the real dump is never "
            "committed."
        ),
        "derived_from": {
            "script": "openremap contribute extractor (wizard)",
            "marker": spec.marker.decode("latin-1"),
        },
        "plants": plants,
        "expect": {"manufacturer": spec.brand, "ecu_family": spec.family},
    }


def render_extractor(spec: Spec) -> str:
    """The extractor skeleton, written to the repository's review bar."""
    spec.validate()
    brand_pkg, family_pkg = spec.package_parts
    exclusions = "\n".join(
        f"    {_bytes_literal(marker)},  # {why}" for marker, why in spec.exclusions
    ) or "    # (none measured — every known-family fixture is collision-free)"

    guard_block = (
        """        # Exclusion guards come first: they are what stops this extractor
        # stealing a binary that another family already identifies correctly.
        # Detection is first-match-wins over an ordered registry, so a missing
        # guard is not a cosmetic problem.
        for guard in EXCLUSION_MARKERS:
            if guard in data[:EXCLUSION_WINDOW]:
                self._set_evidence()
                return False
"""
        if spec.exclusions
        else """        # No exclusion markers were measured for this family. Re-check with
        # `openremap contribute check` before submitting: if another family's
        # fixture starts matching here, you need a guard.
"""
    )

    field_defaults = "\n".join(
        f"        result[{name!r}] = None  # TODO: {hint}"
        for name, hint in (
            spec.fields.get("todos")
            or {
                "software_version": "extract the software version from the binary",
                "hardware_number": "extract the hardware part number",
                "oem_part_number": "extract the OEM part number, if the family has one",
                "calibration_id": "extract the calibration id, if the family has one",
            }
        ).items()
    )

    sw_line = spec.fields.get("software_version_hint")
    sw_comment = (
        f"        # measured candidate: {sw_line}\n" if sw_line else ""
    )

    return f'''"""
{spec.brand} {spec.family} ECU binary extractor.

Generated by ``openremap contribute extractor`` from a contributor's own dumps.
The marker and the size gate below were **measured**; every ``TODO`` was not, and
is the part only the person with the dumps can fill in.

Detection is first-match-wins over an ordered registry, so before merging:
  * place this extractor after every family it could overlap with, and write the
    reason as a comment in the brand's ``EXTRACTORS`` list;
  * keep the exclusion guards below — dropping one hands those binaries to this
    extractor silently.
"""

import hashlib
from typing import Dict, List

from openremap.core.manufacturers.base import (
    BaseManufacturerExtractor,
    EXCLUSION_CLEAR,
    FAMILY_STRING,
    SIZE_MATCH,
)

# The family marker, measured as present in every dump provided and absent from
# every known-family fixture (see the wizard report for the offsets).
FAMILY_MARKER: bytes = {_bytes_literal(spec.marker)}

# File sizes the family is known in. ``can_handle()`` is called for every binary
# identify() sees, so the size gate goes first: it is the cheapest filter there is.
KNOWN_SIZES: tuple = {_sizes_expr(spec.sizes)}

# Markers that mean "this file belongs to someone else".
EXCLUSION_MARKERS: tuple = (
{exclusions}
)

# How much of the file the boundaries are checked in. Bounded on purpose: an
# unbounded scan on every binary is the fastest way to make identify() slow.
HEADER_WINDOW: int = 0x20000
EXCLUSION_WINDOW: int = 0x80000


class {spec.class_name}(BaseManufacturerExtractor):
    """Extractor for {spec.brand} {spec.family}."""

    @property
    def name(self) -> str:
        return {spec.brand!r}

    @property
    def supported_families(self) -> List[str]:
        # List what extract() can actually *report*. A key that no code path
        # returns is a declaration the engine cannot honour — the repository has
        # a guard test for exactly that.
        return [{spec.family!r}]

    # -----------------------------------------------------------------------
    # Detection
    # -----------------------------------------------------------------------

    def can_handle(self, data: bytes) -> bool:
        """Return True only for a {spec.brand} {spec.family} binary.

        Evidence tags describe *why* the binary matched; the confidence scorer
        turns them into a detection-quality bonus. Every ``return False`` clears
        them, so a stale match can never be attributed to the next binary.
        """
        evidence: List[str] = []

{guard_block}        evidence.append(EXCLUSION_CLEAR)

        if KNOWN_SIZES and len(data) not in KNOWN_SIZES:
            self._set_evidence()
            return False
        evidence.append(SIZE_MATCH)

        if FAMILY_MARKER not in data[:HEADER_WINDOW]:
            self._set_evidence()
            return False
        evidence.append(FAMILY_STRING)

        self._set_evidence(evidence)
        return True

    # -----------------------------------------------------------------------
    # Extraction
    # -----------------------------------------------------------------------

    def extract(self, data: bytes, filename: str = "unknown.bin") -> Dict:
        """Extract the identity. Every TODO below needs a real dump to decide."""
        result: Dict = {{
            "manufacturer": self.name,
            "file_size": len(data),
            "md5": hashlib.md5(data).hexdigest(),
            "sha256_first_64kb": hashlib.sha256(data[:0x10000]).hexdigest(),
            "ecu_family": {spec.family!r},
            "ecu_variant": None,
            "calibration_version": None,
            "sw_base_version": None,
            "serial_number": None,
            "dataset_number": None,
        }}

        result["raw_strings"] = self.extract_raw_strings(
            data=data, region=slice(0, HEADER_WINDOW), min_length=8, max_results=20
        )

{sw_comment}{field_defaults}

        result["match_key"] = self.build_match_key(
            ecu_family={spec.family!r},
            ecu_variant=None,
            software_version=result["software_version"],
        )

        return result
'''


def render_test(spec: Spec, fixture_name: str) -> str:
    """The test a reviewer will ask for: fixture-driven, no real dump needed."""
    spec.validate()
    return f'''"""
Tests for {spec.brand} {spec.family} extraction.

Generated by ``openremap contribute extractor``. The fixture is committed, so
these run in CI with no ECU data — and the negative cases matter more than the
positive one: an extractor that matches its own family is easy, one that leaves
every other family alone is the actual requirement.
"""

import json
from pathlib import Path

import pytest

from openremap.core.manufacturers.{spec.package_parts[0]}.{spec.package_parts[1]}.extractor import (
    {spec.class_name},
)

FIXTURES = Path(__file__).resolve().parents[3] / "_dev" / "contrib" / "recipes"
RECIPE = FIXTURES / {fixture_name!r}

EXTRACTOR = {spec.class_name}()


def _fixture_bytes() -> bytes:
    """Rebuild the fixture from its recipe (same rule as build_corpus.py)."""
    recipe = json.loads(RECIPE.read_text(encoding="utf-8"))
    size = int(recipe["file_size"])
    buf = bytearray(bytes.fromhex(recipe.get("fill", "FF")) * size)
    for plant in recipe["plants"]:
        pos = int(plant["at"], 16) if not plant["at"].startswith("end-") else (
            size - int(plant["at"][4:], 16)
        )
        for token in plant["seq"]:
            payload = (
                token["text"].encode("ascii") if "text" in token
                else bytes.fromhex(token["hex"])
            )
            buf[pos : pos + len(payload)] = payload
            pos += len(payload)
    return bytes(buf)


@pytest.fixture(scope="module")
def fixture() -> bytes:
    if not RECIPE.is_file():
        pytest.skip("wizard fixture recipe not present")
    return _fixture_bytes()


def test_accepts_its_own_family(fixture: bytes):
    assert EXTRACTOR.can_handle(fixture) is True
    assert EXTRACTOR.last_detection_evidence  # evidence must be recorded


def test_extract_reports_the_family(fixture: bytes):
    """The contract the engine needs from extract() — no registry required.

    Deliberately *not* routed through ``identify()``: a brand-new extractor is
    not registered yet, so an identity test would be red on a fresh skeleton and
    teach the contributor to ignore red. Reachability has its own test below,
    which skips with instructions until registration is done.
    """
    result = EXTRACTOR.extract(fixture, "fixture.mimic")
    assert result["manufacturer"] == {spec.brand!r}
    assert result["ecu_family"] == {spec.family!r}
    for key in (
        "file_size",
        "md5",
        "sha256_first_64kb",
        "ecu_variant",
        "software_version",
        "hardware_number",
        "calibration_id",
        "oem_part_number",
        "match_key",
    ):
        assert key in result, f"extract() must populate {{key!r}}"


def test_is_reachable_from_the_registry():
    """Registration is a human decision (position is behaviour) — so, skip."""
    from openremap.core.manufacturers import get_extractors

    modules = {{type(extractor).__module__ for extractor in get_extractors()}}
    if __name__ not in modules:
        pytest.skip(
            "not registered yet — add it to the brand's EXTRACTORS list in the "
            "right position, with a comment saying why"
        )


def test_rejects_a_wrong_size(fixture: bytes):
    """The size gate is the cheapest filter; it must actually reject."""
    assert EXTRACTOR.can_handle(fixture[: len(fixture) // 2]) is False


def test_a_false_result_clears_evidence(fixture: bytes):
    """Stale evidence would be attributed to the next binary identify() sees."""
    assert EXTRACTOR.can_handle(fixture) is True
    EXTRACTOR.can_handle(b"\\x00" * 4096)
    assert EXTRACTOR.last_detection_evidence == ()
'''


def render_report(spec: Spec, analysis: dict, verification: dict | None = None) -> str:
    """The pull-request description, assembled from what was measured."""
    lines: list[str] = [
        f"# New family: {spec.brand} {spec.family}",
        "",
        "Generated by `openremap contribute extractor` from the dumps named below.",
        "Read the *Not verified* section before trusting any of it.",
        "",
        "## Dumps measured",
        "",
    ]
    for dump in analysis.get("dumps", []):
        claim = dump.get("claimed_by")
        owner = (
            f"already claimed by `{claim['extractor']}`"
            if claim
            else "not claimed by any extractor"
        )
        lines.append(f"- `{dump['name']}` — {dump['size']} bytes, {owner}")

    lines += ["", "## Chosen marker", "", f"- marker: `{spec.marker.decode('latin-1')}`"]
    if spec.marker_offset is not None:
        lines.append(f"- first offset: `0x{spec.marker_offset:X}`")
    lines += [
        f"- sizes: {', '.join(hex(s) for s in spec.sizes) or 'none measured'}",
        f"- fixture: `{spec.fixture_name}`",
    ]

    collisions = spec.collides_with
    lines += ["", "## Collisions", ""]
    if collisions:
        lines.append(
            "The chosen marker also occurs in known-family fixtures "
            f"({', '.join(collisions)}), so exclusion guards were generated for "
            "them and the skeleton was executed to confirm none of those "
            "fixtures matches any more."
        )
    else:
        lines.append(
            "None: the marker occurs in none of the "
            f"{analysis.get('fixtures_checked', '?')} known-family fixtures."
        )

    if verification:
        lines += ["", "## Mechanical checks", ""]
        for check in verification.get("checks", []):
            mark = "x" if check["ok"] else " "
            lines.append(f"- [{mark}] {check['name']}: {check['detail']}")

    lines += ["", "## Not verified", ""]
    lines.append(
        "- **Correctness.** The fixture proves self-consistency, not that real "
        "ECUs of this family look like this."
    )
    if len(analysis.get("dumps", [])) < 2:
        lines.append(
            "- **Marker stability.** Only one dump was provided, so a marker that "
            "varies between calibrations could not be ruled out."
        )
    if any("TODO" in line for line in spec.notes):
        lines.append("- **Identity fields.** The extractor has TODOs for fields "
                     "the dumps could not decide.")
    lines.append("")
    lines.append(
        "Existing extractors remain the oracle for every other family: the "
        "fixture corpus is our own assumption, and a collision is a warning, "
        "never proof."
    )
    return "\n".join(lines) + "\n"
