"""
openremap.api.composite — composite commands (hand-written compositions).

A command belongs here when it is a *composition*: its non-presentation core
orchestrates multiple independent operations (or crosses multiple service
domains) into one combined result.  Examples:

  analyze  = identify + VIN + layout + maps + checksums + health + coherence
  tune     = validate_before → patch → validate_after
  cook     = diff + map scan + annotation + region tags

Importing a module here registers its methods in the registry (kind="composite").

Contrast:
  command   — atomic: ONE cohesive operation in ONE domain (even if batched
              across a directory, or with internal sub-steps that are not
              separately exposed).
  workflow  — declarative: a linear, unconditional pipeline (a data file, no
              code).

Full classification criteria and the verified list live in ``README.md`` in
this directory — that file is the source of truth for the kind taxonomy.
"""

from openremap.api.composite import (  # noqa: F401
    analyze,
    cook,
    cook_volatile,
    diff_maps,
    health,
    tune,
)

__all__ = ["analyze", "cook", "cook_volatile", "diff_maps", "health", "tune"]
