<!--
Keep this short. A reviewer should be able to see *what* changed and *how it was
checked* in under a minute — the checklist below is the same bar the repository
already applies to itself, and `openremap contribute check` verifies most of it
mechanically.
-->

## What this changes

<!-- One or two lines. Which family, which bug, which behaviour. -->

## How it was checked

<!-- Paste the real output, not a summary. -->

- [ ] `openremap contribute check <extractor.py>` — green (extractor PRs only)
- [ ] `.venv/bin/python -m pytest <the test you touched>` — green
- [ ] Full suite run if shared code changed (recipe schema, patcher, Rust extension)

```
paste the output here
```

## For extractor PRs

- [ ] `openremap contribute extractor … --write` was used, or the recipe was derived by hand
- [ ] The fixture recipe is included and `_dev/contrib/build_corpus.py build` is in sync (`build --check` clean)
- [ ] The registry position is deliberate, **with a comment saying why** — detection is first-match-wins, so position is behaviour
- [ ] Every `return False` in `can_handle()` clears the evidence first
- [ ] `supported_families` lists what `extract()` can actually report
- [ ] Two or more dumps were measured, or the PR says explicitly that stability was not checked

## Not verified

<!--
Say what this PR does NOT establish. For a new family the honest answer is
usually: the fixture proves self-consistency, not that real ECUs of this family
look like this.
-->

## Checklist

- [ ] `changelog/<version>.md` updated for user-visible changes
- [ ] Wiki/docs updated (separate repository) if a command, output or workflow changed
- [ ] No firmware, dump, ROM or proprietary calibration data is included — recipes and synthetic dumps only
- [ ] No third-party identification tool is named anywhere in the diff
