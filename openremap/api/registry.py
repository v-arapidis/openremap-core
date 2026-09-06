"""
openremap.api.registry — the single method/workflow registry and dispatcher.

This is the source of truth for "which methods exist".  Commands and
workflows register here with metadata (name, kind, description, input schema);
the transport dispatches purely by name lookup and knows nothing about the
domain.

Input schema entries::

    {"name": {"type": "int", "required": False, "default": 32,
              "min": 8, "max": 128, "description": "…"}}

Defaults and validation live *here* (in the schema), not in the CLI or GUI, so
every consumer reads the same contract via :func:`list_methods`.
"""

from __future__ import annotations

from typing import Any, Callable

from openremap.api.errors import InvalidParamsError, UnknownMethodError
from openremap.api.serialize import to_json_safe

Method = Callable[..., dict]

_REGISTRY: dict[str, dict[str, Any]] = {}


def register(
    name: str,
    *,
    kind: str = "command",
    description: str = "",
    input_schema: dict[str, dict[str, Any]] | None = None,
    steps: list[str] | None = None,
) -> Callable[[Method], Method]:
    """Decorator: register *name* and return the handler unchanged."""

    def decorator(handler: Method) -> Method:
        _REGISTRY[name] = {
            "name": name,
            "kind": kind,
            "description": description,
            "input_schema": dict(input_schema or {}),
            "steps": list(steps or []),
            "handler": handler,
        }
        return handler

    return decorator


def unregister(name: str) -> None:
    """Remove a method from the registry (used by tests/reloads)."""
    _REGISTRY.pop(name, None)


def get_method(name: str) -> dict[str, Any] | None:
    return _REGISTRY.get(name)


def list_methods(kind: str | None = None) -> list[dict[str, Any]]:
    """Return method metadata (handlers excluded), sorted by name."""
    methods = [
        {key: val for key, val in meta.items() if key != "handler"}
        for meta in _REGISTRY.values()
        if kind is None or meta["kind"] == kind
    ]
    methods.sort(key=lambda m: m["name"])
    return methods


def dispatch(name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Dispatch *name* with *params* and return a JSON-serialisable dict."""
    meta = _REGISTRY.get(name)
    if meta is None:
        raise UnknownMethodError(f"Unknown method: {name!r}")

    merged = _apply_schema(meta, params or {})
    result = meta["handler"](merged)
    return to_json_safe(result)


def call(name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """In-process convenience wrapper around :func:`dispatch`."""
    return dispatch(name, params)


# ---------------------------------------------------------------------------
# Schema handling — defaults + validation in one place.
# ---------------------------------------------------------------------------

_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "str": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "bytes": lambda v: isinstance(v, (bytes, bytearray)),
    "dict": lambda v: isinstance(v, dict),
    "list": lambda v: isinstance(v, (list, tuple)),
}


def _apply_schema(meta: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Merge defaults into *params* and validate each declared input."""
    schema: dict[str, dict[str, Any]] = meta["input_schema"]
    merged = dict(params)

    for pname, spec in schema.items():
        if pname not in merged:
            if "default" in spec:
                merged[pname] = spec["default"]
            elif spec.get("required"):
                raise InvalidParamsError(
                    f"{meta['name']}: missing required param {pname!r}"
                )
            else:
                continue
        _check_value(meta["name"], pname, spec, merged[pname])

    return merged


def _check_value(name: str, pname: str, spec: dict[str, Any], value: Any) -> None:
    expected = spec.get("type")
    check = _TYPE_CHECKS.get(expected)
    if check is not None and not check(value):
        raise InvalidParamsError(
            f"{name}: {pname} must be {expected}, got {type(value).__name__}"
        )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "min" in spec and value < spec["min"]:
            raise InvalidParamsError(f"{name}: {pname} must be >= {spec['min']}")
        if "max" in spec and value > spec["max"]:
            raise InvalidParamsError(f"{name}: {pname} must be <= {spec['max']}")
