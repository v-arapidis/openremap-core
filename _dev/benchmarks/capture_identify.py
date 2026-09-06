"""
Capture identification results for every real binary in tests/data/.

Usage:
    uv run python benchmarks/capture_identify.py [output.json] [--limit N]

Produces a deterministic JSON snapshot (sorted keys, sorted files):
    {
      "generated_at": ...,
      "openremap_version": ...,
      "file_count": N,
      "summary": {tier counts, identified count, avg_ms},
      "files": [
        {
          "path": "tests/data/ECUs/Bosch/EDC17/1__1__1.bin",
          "size": 4194304,
          "sha256": "...",
          "ms": 1234.5,
          "error": null,
          "identity": {family, manufacturer, sw, hw, cal, variant,
                       match_key, oem, detection_strength,
                       detection_evidence},
          "confidence": {"score": 85, "tier": "High",
                         "signals": [[delta, label], ...],
                         "warnings": [...]}
        },
        ...
      ]
    }

Run a second capture after changing the pipeline and diff with
compare_identify.py.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

from openremap import __version__
from openremap.core.services.identify.confidence import score_identity
from openremap.core.services.identify.identifier import identify_ecu

ROOT = Path(__file__).parent.parent

BIN_DIRS = [
    ROOT / "tests" / "data" / "ECUs",
    ROOT / "tests" / "data" / "tune",
]


def capture(limit: int | None = None) -> dict:
    paths: list[Path] = []
    for d in BIN_DIRS:
        if d.exists():
            paths.extend(sorted(p for p in d.rglob("*") if p.is_file()))
    if limit:
        paths = paths[:limit]

    files = []
    identified = 0
    tiers: dict[str, int] = {}
    total_ms = 0.0

    for i, path in enumerate(paths, 1):
        rel = path.relative_to(ROOT)
        data = path.read_bytes()
        entry: dict = {
            "path": str(rel),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "ms": None,
            "error": None,
            "identity": {},
            "confidence": {},
        }

        try:
            t0 = time.perf_counter()
            identity = identify_ecu(data=data, filename=path.name)
            conf = score_identity(identity, filename=path.name, data=data)
            entry["ms"] = round((time.perf_counter() - t0) * 1000, 1)
            total_ms += entry["ms"]

            entry["identity"] = {
                "manufacturer": identity.get("manufacturer"),
                "ecu_family": identity.get("ecu_family"),
                "ecu_variant": identity.get("ecu_variant"),
                "software_version": identity.get("software_version"),
                "hardware_number": identity.get("hardware_number"),
                "calibration_id": identity.get("calibration_id"),
                "match_key": identity.get("match_key"),
                "oem_part_number": identity.get("oem_part_number"),
                "detection_strength": identity.get("detection_strength"),
                "detection_evidence": list(identity.get("detection_evidence") or ()),
            }
            entry["confidence"] = {
                "score": conf.score,
                "tier": conf.tier,
                "signals": [[s.delta, s.label] for s in conf.signals],
                "warnings": list(conf.warnings),
            }
            if conf.tier != "Unknown":
                identified += 1
            tiers[conf.tier] = tiers.get(conf.tier, 0) + 1
        except Exception as exc:  # noqa: BLE001 — capture must never die
            entry["error"] = f"{type(exc).__name__}: {exc}"

        files.append(entry)
        if i % 100 == 0:
            print(f"  … {i}/{len(paths)}", file=sys.stderr)

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "openremap_version": __version__,
        "file_count": len(files),
        "summary": {
            "identified": identified,
            "tiers": tiers,
            "avg_ms": round(total_ms / len(files), 1) if files else 0.0,
        },
        "files": files,
    }


def main() -> None:
    output = (
        Path(sys.argv[1]) if len(sys.argv) > 1
        else ROOT / "benchmarks" / "identify-baseline.json"
    )
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    print(f"Capturing identify results → {output}", file=sys.stderr)
    result = capture(limit=limit)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=1, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    s = result["summary"]
    print(
        f"Done: {result['file_count']} files, {s['identified']} identified, "
        f"tiers={s['tiers']}, avg {s['avg_ms']} ms/file → {output}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
