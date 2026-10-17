"""
openremap.api.commands — hand-written operations (code) exposed as API methods.

Each command is a thin wrapper over the service layer; importing a module here
registers its methods in the registry.  This is the "code" kind — declarative
pipeline compositions live in ``openremap.api.workflows`` instead, and
*compositions* (orchestrating several commands/domains) live in
``openremap.api.composite``.
"""

from openremap.api.commands import (  # noqa: F401
    audit,
    checksum,
    contribute,
    convert,
    identify,
    layout,
    merge,
    meta,
    routine,
    scan,
    scan_maps,
    scan_vins,
    synth,
    tune,
    validate,
)

__all__ = [
    "audit",
    "checksum",
    "contribute",
    "convert",
    "identify",
    "layout",
    "merge",
    "meta",
    "routine",
    "scan",
    "scan_maps",
    "scan_vins",
    "synth",
    "tune",
    "validate",
]
