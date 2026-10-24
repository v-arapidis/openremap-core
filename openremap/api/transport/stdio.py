"""
openremap.api.transport.stdio — NDJSON JSON-RPC loop over stdin/stdout.

Spawn via::

    python -m openremap.api.transport.stdio

Logging goes to stderr only; stdout carries nothing but JSON-RPC responses.
This is the replacement for the legacy ``openremap/server.py`` daemon.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import IO

from openremap.api.transport import jsonrpc

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[openremap-api] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def serve(inp: IO[str], out: IO[str]) -> None:
    """Read one request per line from *inp* and write one response to *out*."""
    for raw in inp:
        raw = raw.strip()
        if not raw:
            continue
        response = jsonrpc.handle_line(raw)
        try:
            out.write(response + "\n")
            out.flush()
        except BrokenPipeError:
            log.info("stdout pipe broken — client disconnected.")
            break
    log.info("stdin closed — shutting down.")


def main() -> None:
    from openremap.console import force_utf8

    # The transport negotiates UTF-8 JSON; the *stream* has to agree, or a
    # non-ASCII byte in a payload becomes an encoding error on Windows.
    force_utf8()
    log.info("openremap-api started (pid=%d)", os.getpid())

    # Restore the default SIGPIPE handler so a broken stdout pipe terminates
    # the process cleanly (POSIX-only; silently skip elsewhere).
    try:
        import signal

        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, OSError):
        pass

    # Run at lower OS priority so a background scan doesn't compete with a UI
    # (POSIX-only; silently skip elsewhere).
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass

    serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
