# Contributing to OpenRemap

First off, thank you for taking the time to contribute. This project is designed
from the ground up to be extended — especially by people who have hands-on
experience with ECU families that are not yet supported.

This page holds the rules that apply to **everything** you might contribute.
Each area of the codebase has its own guide, because the conventions and the
review bar differ a lot between them — see [Domain guides](#domain-guides) below.

---

## 🛡️ Contributor Safety Notice

**Please read this before contributing anything.** To keep OpenRemap sustainable, legally sound, and trustworthy as an open-source project, all contributors must follow these rules.

### No Binary Distribution

Never upload, attach, or link to ECU binary files (`.bin`, `.ori`, `.kp`, `.ols`) in pull requests, issues, or any other part of this repository. ECU firmware is proprietary intellectual property. Do not add any binary files to this repository.

### Original Heuristics Only

All extraction patterns, byte sequences, and regex logic you contribute must be the result of your own independent research or derived from publicly available documentation. Do not copy detection logic, offsets, or patterns from other tools without verifying they are openly licensed.

### No Reverse-Engineered Commercial Logic

Do not submit code, offsets, or algorithms that have been reverse-engineered from commercial or closed-source tuning software (WinOLS, ECM Titanium, Alientech, KESS, and similar). If you are unsure whether your source is acceptable, open an issue and ask before submitting.

### No Damos or A2L Derived Logic

Damos (`.dam`) and A2L files are proprietary Bosch calibration data formats that describe the exact memory layout, addresses, and scaling of ECU parameters. They are not publicly available and are covered by strict NDAs and licensing agreements.

All extraction patterns, byte offsets, and detection logic contributed to this project must be derived from **your own independent analysis of binary files** — not from Damos, A2L, or any other proprietary calibration data source. If anyone asks, and they may, your patterns came from staring at hex dumps, not from a calibration file someone emailed you.

This is not a technicality. Submitting logic derived from proprietary calibration data exposes both you and the project to serious intellectual property claims.

### Research Focus — No Emissions Delete Tools

This project is strictly for research and educational purposes. Pull requests that implement or enable the bypassing of emissions systems, DPF/EGR delete functionality, or any other modification that is illegal under environmental regulations will not be accepted.

### Independent Verification Reminder

Any output produced by this tool — recipes, patched binaries, or identification results — must be verified by a qualified professional and run through a standalone checksum corrector before being flashed to any vehicle. If your contribution changes patch behaviour or output format, update the documentation accordingly.

---

## Ways to Contribute

| Type | Guide | Examples |
|---|---|---|
| **New ECU extractor** | [extractors.md](contributing/extractors.md) | Add support for Siemens SID, Delphi DCM, Marelli MJD, Continental |
| **Checksum profile** | [checksums.md](contributing/checksums.md) | A family whose stored checksum we cannot verify yet |
| **CPU / arch decoder** | [decoders.md](contributing/decoders.md) | A CPU capstone does not cover, or better coverage of one it does |
| **Map hunting / scoring** | [map-scoring.md](contributing/map-scoring.md) | Fewer false positives, better axis pairing, faster scans |
| **Instruction scanner** | [scanners.md](contributing/scanners.md) | A new advisory flag on changed instructions |
| **Tests** | [tests.md](contributing/tests.md) | Coverage for any extractor or service — no ECU dump needed |
| **Bug fix** | — | A crash, a wrong identification, a validation logic error |
| **Documentation** | — | README, docstrings, the wiki repo |

---

## Domain guides

Each guide covers where the code lives, the conventions of that area, and what a
pull request must demonstrate. Start with the one that matches your change.

- **[extractors.md](contributing/extractors.md)** — adding an ECU family. The
  highest-impact contribution, and the one with the most traffic.
- **[checksums.md](contributing/checksums.md)** — adding a checksum profile;
  includes the "family consensus is proof" rule.
- **[decoders.md](contributing/decoders.md)** — CPU decoders, and why they need
  an independent oracle before anything else.
- **[map-scoring.md](contributing/map-scoring.md)** — the Rust map hunter:
  rebuilding, validating against a corpus, and the thresholds that are API.
- **[scanners.md](contributing/scanners.md)** — advisory flags on changed
  instructions.
- **[tests.md](contributing/tests.md)** — where tests go, the golden corpus, and
  how to test without owning a dump.

---

## Getting the Project Running Locally

### Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv)

### Steps

```bash
# 1. Fork the repo on GitHub, then clone your fork
git clone https://github.com/your-username/openremap.git
cd openremap

# 2. Install all dependencies
uv sync

# 3. Run the test suite to confirm everything works
uv run pytest
```

That's all that is needed to work on the engine and CLI. No server, no database, no environment file required.

If you change anything under `openremap/_rs/` (the Rust extension), rebuild it
before running tests — otherwise you are testing the previous binary:

```bash
uv run --with maturin maturin develop --release
```

---

## Code Style

- **Python 3.10+ type hints everywhere.** Use `str | None` rather than `Optional[str]` for new code.
- **Docstrings on all public methods.** Follow the style already present in `base.py` and the validator services — short summary line, then `Args:` / `Returns:` blocks where the function is non-trivial.
- **No bare `except`.** Catch specific exceptions or use `except Exception` with a comment explaining why.
- **No abbreviations in variable names** unless they are universally understood in the domain (`ob`, `mb`, `ctx`, `ecu`, `sw`).
- **Hex strings are always uppercase.** Use `.hex().upper()` and uppercase literals (`"AABBCCDD"`, not `"aabbccdd"`).

---

## Submitting a Pull Request

1. **Fork** the repository and create a branch from `main`:
   ```bash
   git checkout -b feat/siemens-sid206-extractor
   ```

2. **Make your changes.** Keep each PR focused on one thing — one extractor, one bug fix, one feature. Mixed PRs are harder to review and slower to merge.

3. **Test your changes** manually against at least one real binary of the target family, or write automated tests if you can.

4. **Write a clear PR description** that answers:
   - What does this PR do?
   - Which ECU families / binaries does it affect?
   - How did you test it?
   - Any known limitations or edge cases?

5. **Open the PR** against the `main` branch.

---

## Reporting a Bug or Wrong Identification

Open a GitHub Issue and include:

- The CLI command you ran or the endpoint you called
- The output you got
- What you expected instead
- The file size of the binary (do **not** attach the binary itself — see below)
- Any printable ASCII strings visible near the start of the file, if you are comfortable sharing them

For identification bugs, the file size and the first few readable strings from the binary header are usually enough to diagnose the problem without needing the file.

---

## A Note on ECU Binary Files

**Do not attach ECU binary files to issues or pull requests.**

ECU firmware is proprietary. The binaries contain intellectual property belonging to the ECU manufacturer (Bosch, Siemens, Delphi, etc.) and potentially the vehicle OEM. Distributing them publicly — even for debugging — is legally risky for you and for this project.

If you need to share a binary to reproduce a bug, do so privately (e.g. via a direct message to a maintainer) and only after confirming you are the legal owner of the data.

There are no binary files in this repository. Most tests use synthetic in-memory data generated by the test helpers in `tests/conftest.py`; a few behavioural tests additionally use real dumps when a local (git-ignored) `tests/data/` corpus is present, and skip cleanly when it is not.

No ECU dump of your own? Generate a fixture instead — it needs no ECU data at all:

```bash
uv run openremap synth --output demo.bin --size 262144 --maps 6 --seed 42
```

Generated files are deterministic (same parameters + seed ⇒ same bytes), so they are safe to commit and to attach to an issue. They carry no manufacturer fingerprints, so `identify` reports `Unknown` for them — by design.
