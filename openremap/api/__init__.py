"""
openremap.api — the canonical public API surface.

Three ways in, one path out::

    import openremap.api as api
    api.call("ping")           # in-process dispatch (JSON-serialisable dict)
    api.ping()                 # ergonomic direct function
    # …or over JSON-RPC: python -m openremap.api.transport.stdio

Every result returned by :func:`call` is JSON-serialisable, so the in-process
result and the RPC result are equivalent.
"""

from __future__ import annotations

from openremap.api import (  # noqa: F401
    commands,
    composite,
    errors,
    registry,
    serialize,
    workflows,
)
from openremap.api.commands.meta import ping, version
from openremap.api.registry import call, dispatch, list_methods

__all__ = [
    "call",
    "dispatch",
    "list_methods",
    "ping",
    "version",
    "errors",
    "registry",
    "serialize",
    "workflows",
]
