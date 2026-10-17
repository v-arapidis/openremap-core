"""
Execute the rendered skeleton and check it against the review bar.

A generated file that has never been run is worse than no file: it looks like
progress. So the wizard compiles the skeleton it just rendered, instantiates it,
and runs `can_handle()` over two sets of bytes:

* its **own fixture** — must be `True`, otherwise the extractor cannot even
  recognise the family it was written for;
* **every known-family fixture** — must be `False`, otherwise the new extractor
  steals binaries from families that already work. This is the check that makes
  collision handling honest: a marker can pass the substring test and still let a
  guard-free skeleton through, and only running it catches that.

Plus the two rules a reviewer would otherwise police by eye: evidence must be
recorded on a match and **cleared** on a miss (stale evidence is attributed to
the next binary `identify()` sees), and `supported_families` must not declare a
key the extractor cannot report.

The skeleton is executed in-process, from source, in a throwaway namespace. It
is the contributor's own future file, built from their own dump, and the
alternative — asking them to run it and report back — is how skeletons get
merged unverified.
"""

from __future__ import annotations

from dataclasses import dataclass

from openremap.core.manufacturers.base import BaseManufacturerExtractor
from openremap.core.services.wizard.fixtures import KnownFixture, known_fixtures


@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def load_extractor(source: str) -> BaseManufacturerExtractor:
    """Compile *source* and return its extractor instance.

    Raises ValueError with the compiler's own message when the rendered source
    is not valid Python — which is a bug in the wizard, never in the dump, and
    must be loud rather than silently skipped.
    """
    namespace: dict = {"__name__": "openremap_wizard_candidate"}
    try:
        code = compile(source, "<generated extractor>", "exec")
    except SyntaxError as exc:  # pragma: no cover - a template bug
        raise ValueError(f"generated extractor is not valid Python: {exc}") from exc
    try:
        exec(code, namespace)  # noqa: S102 - see module docstring
    except Exception as exc:  # noqa: BLE001
        # Import failures and module-level errors are results, not crashes: a
        # checker that dies on a broken candidate cannot report what is broken.
        raise ValueError(
            f"extractor source failed to import: {type(exc).__name__}: {exc}"
        ) from exc

    for value in namespace.values():
        if (
            isinstance(value, type)
            and issubclass(value, BaseManufacturerExtractor)
            and value is not BaseManufacturerExtractor
        ):
            return value()
    raise ValueError("generated extractor defines no BaseManufacturerExtractor subclass")


def check_extractor(
    extractor: BaseManufacturerExtractor,
    *,
    family: str | None = None,
    own_fixture: bytes | None = None,
    fixtures: tuple[KnownFixture, ...] | None = None,
    expect_exact_families: bool = False,
) -> dict:
    """Run the review bar over a live extractor instance.

    "Steals nothing" is judged against what the extractor **declares**: the Bosch
    EDC16 extractor legitimately accepts all twelve of its own fixtures, so
    counting those as theft would make the check useless for every real
    extractor. A fixture whose family this extractor declares is its own; any
    other fixture it accepts is a genuine collision.
    """
    pool = fixtures if fixtures is not None else known_fixtures()
    declared = list(extractor.supported_families or [])
    checks: list[Check] = []

    if own_fixture is not None:
        accepts_own = extractor.can_handle(own_fixture) is True
        checks.append(
            Check(
                "claims its own fixture",
                accepts_own,
                "can_handle() accepts the fixture built from this family's marker"
                if accepts_own
                else "can_handle() REJECTS its own fixture — the marker or the "
                "size gate is wrong",
            )
        )
        evidence = tuple(extractor.last_detection_evidence)
        checks.append(
            Check(
                "records evidence on a match",
                bool(evidence),
                f"evidence={evidence}" if evidence else "no evidence tags recorded",
            )
        )

    stolen: list[str] = []
    accepted_own = 0
    first_miss: tuple[str, bytes] | None = None
    for fixture in pool:
        if fixture.data == own_fixture:
            continue
        if not extractor.can_handle(fixture.data):
            if first_miss is None:
                first_miss = (fixture.name, fixture.data)
            continue
        if fixture.family in declared or fixture.family_key in declared:
            accepted_own += 1
            continue
        stolen.append(f"{fixture.name} ({fixture.family_key})")

    if not pool:
        # No fixtures means this check cannot be performed at all. Reporting it as
        # a pass would be the most dangerous outcome in the whole tool: the one
        # check that stops a contribution stealing another family's binaries,
        # silently green because the checkout was incomplete.
        checks.append(
            Check(
                "steals no other family",
                False,
                "cannot be checked: no fixtures were available to test against — "
                "either this is not a checkout (no _dev/contrib/recipes/) or every "
                "fixture belongs to this family itself",
            )
        )
    else:
        checks.append(
            Check(
                "steals no other family",
                not stolen,
                f"accepts {accepted_own} of its own fixtures and none of the other "
                f"{len(pool) - accepted_own} known-family fixtures"
                if not stolen
                else f"also accepts {len(stolen)} fixture(s) of families it does not "
                "declare: " + ", ".join(stolen[:4]) + ("…" if len(stolen) > 4 else "")
                + " — add an exclusion guard or fix the marker",
            )
        )

    # Stale evidence is attributed to the next binary identify() sees, so this is
    # checked by re-running a fixture that was just rejected — deliberately, not
    # by reading whatever the loop happened to end on (it ends on whichever
    # fixture sorts last, which for a real extractor is usually its own match).
    if first_miss is None:  # pragma: no cover - would mean it accepts everything
        checks.append(
            Check(
                "clears evidence on a miss",
                False,
                "no fixture was rejected, so the clearing rule could not be tested",
            )
        )
    else:
        name, data = first_miss
        extractor.can_handle(data)
        cleared = extractor.last_detection_evidence == ()
        checks.append(
            Check(
                "clears evidence on a miss",
                cleared,
                f"rejecting {name} leaves no stale evidence behind"
                if cleared
                else f"rejecting {name} left evidence behind "
                f"({extractor.last_detection_evidence}) — a later binary would "
                "inherit it",
            )
        )

    if family is not None:
        reports_family = extractor.extract(b"\x00" * 8, "x.bin").get("ecu_family")
        declares_reportable = family in declared
        checks.append(
            Check(
                "declares the family it reports",
                declares_reportable,
                f"supported_families={declared}"
                if declares_reportable
                else f"extract() reports {reports_family!r} / family {family!r} is "
                f"not in supported_families={declared}",
            )
        )
        if expect_exact_families:
            exact = declared == [family]
            checks.append(
                Check(
                    "declares exactly the family it reports",
                    exact,
                    f"supported_families={declared}"
                    if exact
                    else f"supported_families={declared} but the generated "
                    f"extractor reports only {family!r}",
                )
            )

    return {
        "ok": all(check.ok for check in checks),
        "checks": [check.as_dict() for check in checks],
        "stolen": stolen,
        "family": family,
    }


def verify_generated(
    source: str,
    *,
    family: str,
    own_fixture: bytes,
    fixtures: tuple[KnownFixture, ...] | None = None,
) -> dict:
    """Run the review bar over a rendered skeleton. Never raises for a bad result."""
    try:
        extractor = load_extractor(source)
    except ValueError as exc:
        return {
            "ok": False,
            "checks": [Check("compiles", False, str(exc)).as_dict()],
            "stolen": [],
        }

    result = check_extractor(
        extractor,
        family=family,
        own_fixture=own_fixture,
        fixtures=fixtures,
        expect_exact_families=True,
    )
    result["checks"].insert(
        0, Check("compiles", True, "the rendered source is valid Python").as_dict()
    )
    return result
