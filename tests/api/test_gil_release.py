"""
The native engine must not freeze the caller while it thinks.

Every heavy ``#[pyfunction]`` in ``openremap/_rs/`` is a thin shim around an
``*_inner`` body, and the shim releases the GIL (``py.detach``). Without that,
a 4 MB scan holds the interpreter lock for its whole run: the CLI looks fine,
but any host with an event loop — the TUI — is frozen. That was a real bug:
the MAPS screen showed nothing at all for seven seconds.

This guards the *observable* property rather than the mechanism: while a heavy
call runs on a worker thread, the calling thread keeps making progress.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from openremap.api import call

ROOT = Path(__file__).parent.parent
BIG = ROOT / "data/tune/original.bin"  # 4 MB — the size that made this visible

#: Anything above this means the caller was blocked, not merely scheduled late.
MAX_STALL_SECONDS = 0.30


def _stall_while(work) -> tuple[float, float]:
    """Run *work* on a thread; return (its duration, worst main-thread gap)."""
    done = threading.Event()
    gaps: list[float] = []

    def heartbeat() -> None:
        last = time.perf_counter()
        while not done.is_set():
            time.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    beat = threading.Thread(target=heartbeat, daemon=True)
    beat.start()
    time.sleep(0.05)
    gaps.clear()

    started = time.perf_counter()
    worker = threading.Thread(target=work)
    worker.start()
    worker.join()
    duration = time.perf_counter() - started
    done.set()
    beat.join(timeout=1.0)
    return duration, (max(gaps) if gaps else 0.0)


@pytest.mark.skipif(not BIG.exists(), reason="4 MB test dump absent")
class TestScanDoesNotBlockTheCaller:
    def test_scan_maps_releases_the_gil(self):
        result: dict = {}

        def work() -> None:
            result.update(call("scan_maps", {"path": str(BIG), "min_score": 0.85}))

        duration, stall = _stall_while(work)
        if duration < 0.3:  # pragma: no cover - a faster engine makes this moot
            pytest.skip(f"scan finished in {duration:.2f}s — too fast to measure")
        assert result.get("tables_count") is not None
        assert stall < MAX_STALL_SECONDS, (
            f"caller stalled {stall * 1000:.0f} ms during a {duration:.1f}s scan"
        )
