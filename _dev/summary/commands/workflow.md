# `workflow` — command summary (fast-lookup)

> One-file reference for `openremap workflow`:
> entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.

## Entry & registration

- Command: `openremap workflow` (no arguments, no options)
- Registered in `openremap/core/cli/main.py` via
  `app.command(name="workflow", help="Print a complete step-by-step workflow guide — …")`
  → `openremap/core/cli/commands/workflow.py::workflow()`.
- No args/options; `no_args_is_help` not set (nothing to pass). Always prints
  and exits **0**.

## Flow (top → bottom)

Static-text printer — no service-layer calls, no file I/O, no error paths.
The whole body is built from module-private helpers that only use
`typer.echo` / `typer.style`:

1. **Header** — `_blank()` → bold `"  OpenRemap — Workflow Guide"` → `_sep()`
   (dim `─`×73 line) → four `_body()` intro lines (walkthrough promise +
   pointers to `--help`, `commands`, `families`) → `_sep()`.
2. **Step 0 — Organise a collection (optional)** — `_step("0", …)` prints
   `blank + sep + blank + "  STEP 0 — <title>"` (bold cyan) + blank; then
   `_body()` What/Why/Tip paragraphs; `_cmd(...)` prints the 4 example
   commands (bold green, 4-space indent) followed by a trailing blank line;
   `_what_to_look_for()` prints `"  What to look for:"`; then `_ok()` (green
   `✓`), `_fail()` (red `✗`) and `_note()` (dim, 7-space indent) hint lines.
3. **Step 1 — Identify your stock binary** — same shape: `_step("1", …)`,
   `_body`, single `_cmd("openremap identify stock.bin")`,
   `_what_to_look_for`, ✓/✗/note hints.
4. **Step 2 — Cook a recipe** — `_step("2", …)`, `_body`,
   `_cmd("openremap cook stock.bin stage1.bin --output recipe.remap")`,
   ✓/✗/note hints.
5. **Step 3 — Apply the recipe (validate → apply → verify in one shot)** —
   `_step("3", …)`, `_body` describing tune's three phases, `_cmd` with 3
   `openremap tune` variants, hints incl. a multi-line `_note` block quoting
   the `openremap validate check` verdicts (EXACT / SHIFTED / MISSING).
6. **Step 4 — Validate individually (advanced)** — `_step("4", …)`, `_body`
   describing `validate before/check/after`, `_cmd` with 4 examples, hints.
7. **Step 5 — MANDATORY checksum warning** — **not** rendered via `_step()`:
   hand-rolled `_blank(); _sep(); _blank()` then a yellow bold
   `"  ⚠  STEP 5 — MANDATORY: correct checksums before flashing"` echo +
   blank + `_body()` lines (OpenRemap does no checksum correction; ECM
   Titanium / WinOLS / Checksum Fix Pro; brick warning).
8. **Footer** — `_blank(); _sep();` `_body("Quick reference:  openremap
   commands", "Full reference:   openremap <command> --help   or   the wiki
   (docs.openremap.com)"); _sep(); _blank()`.

## Expected output

Prints exactly these section headings (bold cyan except Step 5 in bold
yellow), each preceded by a separator line:

```
STEP 0 — Organise a collection  (optional — skip if you have your files ready)
STEP 1 — Identify your stock binary
STEP 2 — Cook a recipe
STEP 3 — Apply the recipe  (validate → apply → verify in one shot)
STEP 4 — Validate individually  (advanced — only when Step 3 fails)
⚠  STEP 5 — MANDATORY: correct checksums before flashing
```

plus an `OpenRemap — Workflow Guide` header and a `Quick reference` /
`Full reference` footer. Each step body: `What:` / `Why:` (and `Tip:` for
Step 0) paragraphs, bold-green example commands, and `What to look for:` with
`✓` / `✗` hints plus dim `_note` elaborations. Exit code always **0**.

## Shared dependencies → other consumers

None. `workflow()` prints static text and touches no shared function — the
only imports are `typer` (echo/style/colors) and stdlib
`from __future__ import annotations`. The sole cross-file reference is the
registration import in `openremap/core/cli/main.py` (line 53 + `app.command`
at line 116): nothing else consumes it.

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `_sep/_blank/_body/_cmd/_ok/_fail/_note/_step/_what_to_look_for` | `workflow.py` (module-private) | nothing — defined and used only inside this module |
| `workflow()` | `workflow.py` | `cli/main.py` (import line 53 + `app.command(name="workflow")` line 116) — sole consumer |

## Gotchas

- Step 5 does **not** go through `_step()` — it is hand-rendered with a `⚠`
  prefix in bold yellow. `_step()`'s `warning=True` parameter (which would
  render yellow) exists but is **never used** — dead code in this module.
- All guide text is **hardcoded**: the examples, quoted outputs
  (`"Recipe built successfully"`, `"Tune complete"`, EXACT/SHIFTED/MISSING
  verdicts), folder names (`scanned/`, `sw_missing/`, `contested/`,
  `unknown/`), and the wiki URL `docs.openremap.com` are static strings. If
  those commands or outputs change, this guide must be edited by hand.
- The `openremap --help` summary for this command comes from the `help=`
  kwarg in `main.py` (lines 116–123), **not** the docstring in `workflow.py`.
- `_cmd()` appends a trailing blank line after the command block so
  "What to look for:" stands apart — intended layout, not a bug.
- No args/options and no error paths — always exits 0; no `--json` or
  colour-stripping variant.
