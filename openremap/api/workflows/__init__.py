"""
openremap.api.workflows — declarative ``.toml`` pipelines (kind="workflow").

A workflow is a **data file** (TOML) — a linear, unconditional list of steps,
each a ``call`` to a registered method with data plumbing.  No code, no
branching, no loops.  If a pipeline needs control flow → make it a
``composite`` (code) instead.

DSL::

    name = "identify_cook"
    description = "Identify both binaries, then cook a recipe in one call."
    # output = "original_id"      # optional; default = last step's result

    [[steps]]
    call = "identify"
    params = { path = "$input.original_path" }
    as = "original_id"            # optional: bind result for later $original_id.*

    [[steps]]
    call = "cook"
    [steps.params]
    original_path = "$input.original_path"
    modified_path = "$input.modified_path"

References inside ``params``:

- ``$input.<key>`` — the workflow's input params (the call's ``params``).
- ``$prev.<key>``  — the immediately previous step's result.
- ``$<as>.<key>``  — a named step's result (bound via ``as = "<name>"``).

A whole-string reference resolves to the referenced value (type-preserving);
an embedded reference (inside a larger string) is string-substituted.

Loading: importing this package auto-loads every ``*.toml`` in the built-in
dir (this directory) and registers it with ``kind="workflow"``.  Extra user
dirs are loaded via :func:`load_workflows` or the ``OPENREMAP_WORKFLOW_DIRS``
env var (colon-separated).  A later dir overrides an earlier one on name
collision.  The built-in dir fails fast on a malformed file (a broken shipped
workflow is a bug); a malformed or invalid ``.toml`` in a user-supplied dir is
skipped with a warning, so one bad third-party file cannot break
``import openremap.api``.
"""

from __future__ import annotations

import os
import re
import warnings
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from openremap.api.errors import InvalidParamsError
from openremap.api.registry import dispatch, register

BUILTIN_DIR = Path(__file__).resolve().parent
USER_DIRS_ENV = "OPENREMAP_WORKFLOW_DIRS"

_WHOLE_REF = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)$")
_EMBED_REF = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")

#: ``as`` names that would shadow the special ``$input``/``$prev`` sources.
_RESERVED_SOURCES = frozenset({"input", "prev"})


class WorkflowError(ValueError):
    """A workflow file is malformed or misconfigured."""


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------


def _lookup(src: str, key: str, input_params: dict, bound: dict, prev_result) -> object:
    if src == "input":
        if key not in input_params:
            raise InvalidParamsError(f"workflow: missing input {key!r}")
        return input_params[key]
    if src == "prev":
        if prev_result is None or key not in prev_result:
            raise InvalidParamsError(f"workflow: ${src}.{key} is not available")
        return prev_result[key]
    if src in bound:
        if key not in bound[src]:
            raise InvalidParamsError(f"workflow: ${src}.{key} is not available")
        return bound[src][key]
    raise InvalidParamsError(f"workflow: unknown reference source ${src}")


def _resolve_string(s: str, input_params: dict, bound: dict, prev_result) -> object:
    m = _WHOLE_REF.match(s)
    if m:
        return _lookup(m.group(1), m.group(2), input_params, bound, prev_result)
    out = s
    for m in _EMBED_REF.finditer(s):
        value = _lookup(m.group(1), m.group(2), input_params, bound, prev_result)
        out = out.replace(m.group(0), str(value))
    return out


def _resolve(value, input_params: dict, bound: dict, prev_result) -> object:
    if isinstance(value, str):
        return _resolve_string(value, input_params, bound, prev_result)
    if isinstance(value, dict):
        return {k: _resolve(v, input_params, bound, prev_result) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, input_params, bound, prev_result) for v in value]
    return value


# ---------------------------------------------------------------------------
# Compile + run
# ---------------------------------------------------------------------------


def _run(spec: dict, params: dict) -> dict:
    input_params = dict(params)
    bound: dict = {}
    prev_result = None
    result = None
    for step in spec["steps"]:
        resolved = _resolve(step.get("params", {}), input_params, bound, prev_result)
        result = dispatch(step["call"], resolved)
        prev_result = result
        if "as" in step:
            bound[step["as"]] = result

    output = spec.get("output")
    if output:
        if output not in bound:
            raise WorkflowError(
                f"workflow {spec['name']!r}: output {output!r} is not a bound step ('as')"
            )
        return bound[output]
    return result


def _collect_inputs(spec: dict) -> set[str]:
    keys: set[str] = set()

    def walk(value) -> None:
        if isinstance(value, str):
            for m in _EMBED_REF.finditer(value):
                if m.group(1) == "input":
                    keys.add(m.group(2))
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    for step in spec.get("steps", []):
        walk(step.get("params", {}))
    return keys


def _validate(spec: dict, source: Path) -> tuple[str, list[dict]]:
    name = spec.get("name")
    if not isinstance(name, str) or not name:
        raise WorkflowError(f"{source}: missing 'name'")
    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        raise WorkflowError(f"{source}: missing '[[steps]]'")
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise WorkflowError(f"{source}: step {i} is not a table")
        call = step.get("call")
        if not isinstance(call, str) or not call:
            raise WorkflowError(f"{source}: step {i} missing 'call'")
        as_name = step.get("as")
        if as_name is not None and as_name in _RESERVED_SOURCES:
            raise WorkflowError(f"{source}: step {i} 'as' name {as_name!r} is reserved")
        if not isinstance(step.get("params", {}), dict):
            raise WorkflowError(f"{source}: step {i} 'params' must be a table")
    return name, steps


def _compile_and_register(spec: dict, source: Path) -> str:
    name, steps = _validate(spec, source)
    description = spec.get("description", "")
    step_calls = [s["call"] for s in steps]
    input_schema = {k: {"required": True} for k in _collect_inputs(spec)}

    def handler(params: dict) -> dict:
        return _run(spec, params)

    register(
        name,
        kind="workflow",
        description=description,
        input_schema=input_schema,
        steps=step_calls,
    )(handler)
    return name


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_directory(directory: Path, *, fail_fast: bool) -> list[str]:
    """Register every ``*.toml`` workflow in *directory*; return their names.

    With ``fail_fast`` (used for the built-in dir) a malformed or invalid file
    raises, so a broken shipped workflow is caught immediately.  User-supplied
    dirs load tolerantly: the offending file is skipped with a warning and the
    rest of the directory still loads — the third-party extension surface must
    not be able to take down ``import openremap.api``.
    """
    names: list[str] = []
    for path in sorted(directory.glob("*.toml")):
        try:
            with open(path, "rb") as fh:
                spec = tomllib.load(fh)
            names.append(_compile_and_register(spec, path))
        except (tomllib.TOMLDecodeError, WorkflowError) as exc:
            if fail_fast:
                raise
            warnings.warn(
                f"workflow: skipping {path}: {exc}", UserWarning, stacklevel=2
            )
    return names


def load_workflows(directories: list[str | Path] | None = None) -> list[str]:
    """Load ``*.toml`` workflows from the built-in dir + extra dirs.

    Returns the names of every workflow registered (later dirs override
    earlier ones on name collision).  The built-in dir fails fast on a
    malformed file; user-supplied dirs (via *directories* or the
    ``OPENREMAP_WORKFLOW_DIRS`` env var) skip bad files with a warning.
    """
    names = _load_directory(BUILTIN_DIR, fail_fast=True)
    extras: list[Path] = [Path(d) for d in (directories or [])]
    env = os.environ.get(USER_DIRS_ENV)
    if env:
        extras.extend(Path(p) for p in env.split(os.pathsep) if p)
    for directory in extras:
        if directory.is_dir():
            names.extend(_load_directory(directory, fail_fast=False))
    return names


# Auto-load the built-in workflows on import, mirroring commands/composite.
load_workflows()
