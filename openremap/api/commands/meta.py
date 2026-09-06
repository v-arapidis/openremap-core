"""
openremap.api.commands.meta — meta commands: ``ping`` and ``version``.
"""

from __future__ import annotations

from openremap.api.registry import list_methods as _registry_list_methods
from openremap.api.registry import register


@register(
    "ping",
    kind="command",
    description="Liveness check — always returns {'ok': True}.",
)
def ping(params: dict | None = None) -> dict:
    return {"ok": True}


@register(
    "version",
    kind="command",
    description="API/library version and the active native backend.",
)
def version(params: dict | None = None) -> dict:
    # Deferred import: keeps API import fast and avoids loading the Rust
    # extension until it is actually asked for.
    from openremap import __version__, _active_backend

    return {"version": __version__, "backend": _active_backend()}


@register(
    "list_methods",
    kind="command",
    description="List all registered methods (commands and workflows) with metadata.",
)
def list_methods(params: dict | None = None) -> dict:
    return {"methods": _registry_list_methods()}
