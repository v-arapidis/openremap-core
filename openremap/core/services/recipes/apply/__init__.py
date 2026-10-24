"""
openremap.core.services.recipes.apply — the new apply engine.

Applies a ``.remap`` recipe to a target binary under an **explicit identity
policy**. It replaces the flag-driven apply engine (``--force``,
``--skip-validation`` and the ``same_file_only`` stamp interacting invisibly),
which has been deleted.

Parity with that engine is preserved as data, not as code:
``tests/tuning/parity_goldens.json`` records the patched ``sha256`` it produced
for 47 real stock/tuned pairs, and ``TestRecordedParity`` asserts the new engine
reproduces every one of them byte for byte.

Layout
------
``policy.py``   the identity policies (file / firmware / any) and their rules
``engine.py``   the pipeline: policy gate -> strict ob -> anchor search -> verify
``report.py``   the report shape: policy block + per-instruction results

Layer rules (AGENTS.md)
-----------------------
Bytes and dicts in and out — no file paths, no I/O, no hidden state. The
engine COMPOSES the existing primitives (``ECUStrictValidator``,
``ECUPatcher``, ``validate_patched``); it never reimplements them, so there is
exactly one safety core in the codebase.

Terminology: these are **policies**, not workflows. In this codebase
"workflow" already means a declarative TOML pipeline registered with
``kind="workflow"`` (``openremap/api/workflows/``). Two meanings for one word
inside one product is how vocabulary rots — do not reuse it here.

Commitments — no silent loss (agreed 2026-10-19)
-----------------------------------------------
These bind this engine AND the future cook that writes 5.0 recipes. They
exist because the two failure directions are not equal: a wrong checksum is
visible and fixable; a silently dropped piece of the tune is neither.

1. **A field nobody reads is a field that can only lie.** Checksum data was
   removed from the recipe entirely: nothing in apply could act on it, and a
   wrong region would have silently dropped real tune bytes. Checksums are
   detected live over a binary, never carried from the donor's dump.
2. **No silent loss, ever.** An invariant test asserts that kept bytes plus
   excluded bytes equal the diff blocks. If a byte disappears, the suite
   fails.
3. **Exclusion, when it exists, requires FULL containment.** A change may be
   excluded only when every one of its bytes lies inside the declared range. A
   partial overlap is KEPT and marked ``needs_review``. (No exclusion ships
   today: the v1 cook drops nothing.)
4. **Exclusion lives only in cook.** This engine never drops, skips or refuses
   an instruction because of "volatile" reasoning of any kind.
5. **No auto-repair.** Recomputing a checksum for a target is out of scope
   here; it is a separate capability over a binary, not part of applying a
   recipe.
6. **Schema freeze.** A major bump (5.0 -> 6.0) requires a SEMANTIC change:
   the meaning of an existing field changes, or a semantic field is removed
   or renamed. Additive changes stay within 5.x.
"""

__all__: list[str] = []
