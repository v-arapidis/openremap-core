"""
``openremap.core.services.wizard`` — the contribution wizard's engine.

**What it is for.** A contributor owns a dump of an ECU family the engine does
not handle yet. The hard part is not writing the `if` — it is knowing *which
bytes are stable across that family and absent from every family we already
recognise*. This package measures that, and renders the two artifacts a pull
request needs: a committable fixture (never their ROM) and an extractor skeleton
that already satisfies the repository's review bar.

Two halves, deliberately separable — and only one of them needs a checkout:

* **measure** (:mod:`~openremap.core.services.wizard.report`) — who claims the
  dump today, the runs stable across the contributor's dumps, candidate identity
  fields. Pure local computation: works from any install.
* **collide + render** (:mod:`~openremap.core.services.wizard.collisions`,
  :mod:`~openremap.core.services.wizard.plan`,
  :mod:`~openremap.core.services.wizard.emit`,
  :mod:`~openremap.core.services.wizard.verify`) — needs the repository's fixture
  recipes, because "is this marker safe?" is a question about every family we
  already recognise. Contributing means opening a pull request, so a checkout is
  a given; without one the wizard says what it could not check instead of
  guessing.

:func:`prepare` is the whole pass in one call (measure → spec → render → verify),
:func:`check_source` / :func:`check_file` run the same review bar over an
extractor that already exists.

Everything here is pure (`bytes`/`dict` in, `dict`/`str` out) except
:mod:`~openremap.core.services.wizard.fixtures`, which reads the checkout's
recipe files, and :func:`check_file`, which reads the file it was asked about.
"""

from openremap.core.services.wizard.check import check_file, check_source
from openremap.core.services.wizard.fixtures import (
    KnownFixture,
    build_fixture,
    find_recipes_dir,
    fixture_count,
    fixture_specs,
    fixtures_available,
    known_fixtures,
    reset_cache,
)
from openremap.core.services.wizard.plan import prepare
from openremap.core.services.wizard.report import analyze
from openremap.core.services.wizard.verify import check_extractor, verify_generated

__all__ = [
    "KnownFixture",
    "analyze",
    "build_fixture",
    "check_extractor",
    "check_file",
    "check_source",
    "find_recipes_dir",
    "fixture_count",
    "fixture_specs",
    "fixtures_available",
    "known_fixtures",
    "prepare",
    "reset_cache",
    "verify_generated",
]
