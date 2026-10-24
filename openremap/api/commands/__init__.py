"""
openremap.api.commands — hand-written operations (code) exposed as API methods.

Each command is a thin wrapper over the service layer; importing a module here
registers its methods in the registry.  This is the "code" kind — declarative
pipeline compositions live in ``openremap.api.workflows`` instead, and
*compositions* (orchestrating several commands/domains) live in
``openremap.api.composite``.
"""

from openremap.api.commands import (
    audit,
    checksum,
    contribute,
    convert,
    families,
    hex_region,
    identify,
    layout,
    map_grid,
    map_image,
    merge,
    meta,
    recipe_info,
    routine,
    scan,
    scan_maps,
    scan_vins,
    synth,
    validate,
)

__all__ = [
    "audit",
    "checksum",
    "contribute",
    "convert",
    "families",
    "hex_region",
    "identify",
    "layout",
    "map_grid",
    "map_image",
    "merge",
    "meta",
    "recipe_info",
    "routine",
    "scan",
    "scan_maps",
    "scan_vins",
    "synth",
    "validate",
]
