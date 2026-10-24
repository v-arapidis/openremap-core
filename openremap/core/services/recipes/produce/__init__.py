"""
openremap.core.services.recipes.produce — the new recipe producer.

Builds a **schema 5.0** recipe from a stock/tuned pair, under an explicit cook
mode. Mirrors the apply package: modes -> builder -> report, with the contract
written and verified here.

Design commitments (the evidence behind each is in the changelog and the
internal specs):

1. **Nothing is dropped.** No exclusion of any kind ships in v1. Measured:
   checksum-store overlaps 0/47 pairs, VIN detections 0/1416 real dumps, and the
   `--exclude-uncertain` heuristic would have removed 41 of 79 real map changes
   from the reference tune. Everything suspicious becomes a *flag* on the
   instruction, which is annotation — never a removal.
2. **A recipe may only be born from one firmware.** If both binaries identify
   and their `match_key` differs, the cook refuses. Without this rule,
   ``identity_floor: any`` would be a licence for nonsense: the deprecated cook
   happily produced a 4-instruction recipe from an EDC16 stock and a SIMOS
   tuned file, with only a warning.
3. **The producer verifies its own output.** Every recipe is checked against
   the apply-side reader (``apply.contract.read_contract``) before it is
   returned — a producer that emits something its consumer rejects is a bug,
   not a surprise for the operator.
4. **The contract has three fields, each with a consumer**: `identity_floor`
   (the floor apply enforces), `anchors_unique` (re-derived by apply to catch a
   doctored contract), `sections` (drives the unknown-section check).

Layout
------
``modes.py``    the two cook modes and what each one demands
``builder.py``  the pipeline: guards -> diff/anchors -> annotate -> maps/regions -> 5.0
``contract.py`` writes the contract and self-checks it through the reader
``report.py``   the structured cook report (counts, flags by kind, warnings)
"""

__all__: list[str] = []
