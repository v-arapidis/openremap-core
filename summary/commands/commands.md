# `commands` — command summary (fast-lookup)

> One-file reference for `openremap commands`:
> entry point, exact call flow, expected output, and every shared function
> it touches (so a change to any of them can be checked against all
> consumers).  Keep this file updated when the command or its dependencies
> change.

## Entry & registration

- Command: `openremap commands` (no arguments, no options)
- Registered in `openremap/core/cli/main.py` via
  `app.command(name="commands", help="Print a one-line-per-command cheat-sheet of every available command.")`
  → `openremap/core/cli/commands/cmds.py::commands()`.
- No args/options; always prints and exits **0**.

## Flow (top → bottom)

Static-text printer — no service-layer calls, no file I/O, no error paths.
Only uses `typer.echo` / `typer.style`:

1. **Deferred app import** — `from openremap.core.cli.main import app`
   INSIDE the function.  A top-level import would be circular (main.py
   imports cmds.py at module load); by the time the function runs the app
   is fully built.
2. **Header** — `_blank()` → bold `"  OpenRemap — Command Reference"` →
   `_sep()` (dim `─`×73) → two intro lines (pointers to `--help` and
   `workflow`, emitted as one multi-line string) → `_sep()` → `_blank()`.
3. **Rows derived from the Typer registry** — never a hardcoded list:
   - start with the bare `("openremap", "Launch the full terminal UI …")`
     TUI entry;
   - for each `app.registered_commands` → `("openremap <name>",
     _describe(info))`;
   - for each `app.registered_groups` → a group line only when
     `isinstance(group.help, str)`, then one row per subcommand
     (`("openremap <group> <sub>", _describe(sub))`).
   `_describe()` returns `info.help` or `info.short_help`, else the
   callback docstring's first non-empty line (the `validate` sub-app sets
   no `help=` — its docstring carries the description).
4. **Column sizing** — `max_syn = max(len(syn) for syn, _ in rows)`, then
   `col = max_syn + 3` padding.
5. **Cheat-sheet body** — iterate `rows`: each syntax bold green,
   right-padded to `col`, description dim, printed as
   `f"  {syn_styled}{' ' * pad}{desc_styled}"`. No blank lines between rows.
6. **Footer** — `_blank(); _sep();` `"Tip: "` (bold) + `"new user? Run  "` +
   bold-green `openremap workflow` + `"  — it walks you through every step."`;
   `_sep(); _blank()`.

## Expected output

```
  OpenRemap — Command Reference
  ─────────────────────────────   (dim 73-char separator)
  Run  openremap <command> --help  for full options on any command.
  Run  openremap workflow           for the complete step-by-step guide.
  ─────────────────────────────
  <N aligned rows: syntax (bold green, right-padded) + description (dim)>
  ─────────────────────────────
  Tip: new user? Run  openremap workflow  — it walks you through every step.
  ─────────────────────────────
```

Command-name lines (syntax column, in printed order): `openremap` (TUI) ·
`openremap commands` · `openremap workflow` · `openremap families` ·
`openremap identify` · `openremap analyze` · `openremap convert` ·
`openremap audit` · `openremap layout` · `openremap merge` · `openremap cook`
· `openremap cook-volatile` · `openremap tune` · `openremap scan` ·
`openremap checksum` · `openremap health` · `openremap scan-vins` ·
`openremap routine` · `openremap scan-maps` · `openremap diff-maps` ·
`openremap validate before` · `openremap validate check` ·
`openremap validate after` · `openremap validate strict` ·
`openremap validate exists` · `openremap validate tuned`.  Exit code always
**0**.  (Descriptions are the commands' full help texts, not curated
one-liners — the rows are longer than the old hardcoded cheat-sheet.)

## Shared dependencies → other consumers

| Function | File | Also used by (edit → check these) |
|---|---|---|
| `_sep/_blank/_describe` | `cmds.py` (module-private) | nothing — defined and used only inside this module |
| `commands()` | `cmds.py` | `cli/main.py` (import + `app.command(name="commands")`) — sole consumer |
| `app` (the Typer app) | `cli/main.py` | **the whole CLI** — this command reads its registry (`registered_commands` / `registered_groups`) at call time, so `commands()` output follows every command registration automatically |

## Gotchas

- The cheat-sheet is **auto-derived** from the Typer app registry — adding
  or renaming a command in `main.py` (or a `validate` sub-app command)
  updates the cheat-sheet automatically, including its `help=` text.
  (Fixed 2026-08-29: it used to be a hardcoded `_COMMANDS` list that
  drifted — it omitted `analyze`/`convert`/`routine`.)
- The `app` import is **deferred inside the function** to dodge the
  main.py↔cmds.py circular import — a future import-order refactor of
  main.py could break it.
- `_describe()` falls back to the callback docstring's first non-empty
  line — if a command's docstring begins with its usage line, the
  cheat-sheet row gets a poor description.  Prefer setting `help=` in
  `main.py`.
- Row alignment depends on `max_syn + 3` padding computed at runtime —
  adding a long syntax shifts every description column.
- The `openremap --help` summary for this command comes from the `help=`
  kwarg in `main.py`, **not** the docstring in `cmds.py`.
- No args/options and no error paths — always exits 0; no `--json` variant.
