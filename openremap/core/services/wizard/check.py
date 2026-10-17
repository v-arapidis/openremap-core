"""
`openremap contribute check` — the review bar over an existing extractor.

Two kinds of check, and the split matters:

* **runtime** (:mod:`~openremap.core.services.wizard.verify`) — does the
  extractor claim its own fixture, does it steal another family's, does a
  rejected binary leave stale evidence behind. These are facts about behaviour.
* **static** (this module) — the rules a reviewer otherwise polices by eye while
  reading a diff: every ``return False`` in ``can_handle()`` clears the evidence
  first, and the extractor is actually reachable from the registry (a perfect
  extractor that nobody imports changes nothing).

The static half is deliberately narrow. It parses the module with :mod:`ast`
rather than matching text, so reformatting or comments cannot fool it, and it
only checks rules that are mechanical — anything needing judgement ("is this
marker distinctive?") is the measurement pass's job, not a lint's.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from openremap.core.services.wizard.verify import Check, check_extractor, load_extractor


@dataclass
class StaticFinding:
    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def _can_handle_node(tree: ast.AST) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "can_handle":
                return node  # type: ignore[return-value]
    return None


def _clears_evidence(stmt: ast.stmt) -> bool:
    """True for ``self._set_evidence()`` with no arguments."""
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return False
    call = stmt.value
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "_set_evidence":
        return False
    return not call.args and not call.keywords


def check_evidence_clearing(source: str) -> StaticFinding:
    """Every ``return False`` in ``can_handle()`` must clear the evidence first.

    This is the rule that causes silent misattribution when broken: the evidence
    scorer reads whatever the last ``can_handle()`` stored, so a miss that leaves
    tags behind credits the *next* binary with a match it never had.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return StaticFinding("evidence cleared on every False", False, f"unparsable: {exc}")

    func = _can_handle_node(tree)
    if func is None:
        return StaticFinding(
            "evidence cleared on every False", False, "no can_handle() found"
        )

    offenders: list[int] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        value = node.value
        is_false = isinstance(value, ast.Constant) and value.value is False
        if not is_false:
            continue
        # The clearing call is the statement just before the return, in the same
        # block — which is how every extractor in the repository writes it.
        block = _block_containing(func, node)
        if block is None:
            offenders.append(getattr(node, "lineno", 0))
            continue
        index = block.index(node)
        if index == 0 or not _clears_evidence(block[index - 1]):
            offenders.append(getattr(node, "lineno", 0))

    if offenders:
        return StaticFinding(
            "evidence cleared on every False",
            False,
            "these `return False` lines do not call self._set_evidence() first: "
            + ", ".join(str(line) for line in offenders),
        )
    return StaticFinding(
        "evidence cleared on every False",
        True,
        "every False path clears the evidence before returning",
    )


def _block_containing(func: ast.AST, target: ast.AST) -> list[ast.stmt] | None:
    for node in ast.walk(func):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and target in block:
                return block
    return None


def check_registered(module_name: str) -> StaticFinding:
    """Is the extractor reachable? An unimported extractor identifies nothing."""
    from openremap.core.manufacturers import get_extractors

    modules = {type(extractor).__module__ for extractor in get_extractors()}
    if module_name in modules:
        return StaticFinding(
            "reachable from the registry", True, f"{module_name} is in BUILTIN_EXTRACTORS"
        )
    return StaticFinding(
        "reachable from the registry",
        False,
        f"{module_name} is not imported by any brand package — add it to the "
        "brand's EXTRACTORS list (and the brand to manufacturers/__init__.py for "
        "a new brand)",
    )


def check_source(source: str, *, module_name: str) -> dict:
    """The full bar for a source file: runtime + static."""
    static = [check_evidence_clearing(source), check_registered(module_name)]
    try:
        extractor = load_extractor(source)
    except ValueError as exc:
        return {
            "ok": False,
            "module": module_name,
            "checks": [finding.as_dict() for finding in static]
            + [Check("compiles", False, str(exc)).as_dict()],
        }

    family = None
    declared = list(extractor.supported_families or [])
    try:
        family = extractor.extract(b"\x00" * 8, "unknown.bin").get("ecu_family")
    except Exception:  # noqa: BLE001 - extraction on a stub is allowed to fail
        family = declared[0] if declared else None

    from openremap.core.services.wizard.fixtures import known_fixtures

    pool = known_fixtures()
    own = next(
        (f.data for f in pool if f.family == family or f.family_key == family), None
    )
    if not pool:
        runtime = check_extractor(extractor, family=family, own_fixture=None, fixtures=())
        return {
            "ok": False,
            "module": module_name,
            "family": family,
            "checks": [finding.as_dict() for finding in static] + runtime["checks"]
            + [
                Check(
                    "fixture available",
                    False,
                    "no fixture recipes found (_dev/contrib/recipes/) — run this "
                    "from a checkout of openremap-core",
                ).as_dict()
            ],
            "stolen": [],
        }

    runtime = check_extractor(extractor, family=family, own_fixture=own, fixtures=pool)
    checks = [finding.as_dict() for finding in static] + runtime["checks"]
    return {
        "ok": all(check["ok"] for check in checks),
        "module": module_name,
        "family": family,
        "checks": checks,
        "stolen": runtime["stolen"],
    }


def check_file(path: Path) -> dict:
    """Check an extractor module on disk."""
    source = path.read_text(encoding="utf-8")
    parts = path.resolve().parts
    module_name = (
        ".".join(parts[parts.index("openremap") : -1]) + ".extractor"
        if "openremap" in parts
        else path.stem
    )
    result = check_source(source, module_name=module_name)
    result["path"] = str(path)
    return result
