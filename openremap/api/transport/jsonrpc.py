"""
openremap.api.transport.jsonrpc — JSON-RPC framing + dispatch + error mapping.

Wire protocol (line-delimited JSON, one object per line, no batching)::

    Request  → {"id": <any>, "method": "<name>", "params": {<obj>}}
    Response ← {"id": <id>, "result": <json>}
             | {"id": <id>, "error": {"code": <int>, "message": "<str>"}}

This module knows nothing about the domain — it parses the line, looks the
method up in the registry, and formats the response.  Every transport (stdio
now, http later) shares it.

Note: the legacy ``openremap/server.py`` returned a bare error *string*; the
new contract uses a JSON-RPC 2.0 error object so clients can branch on
``code``.  Phase 4 (transport cutover) must coordinate this change with any
existing client.
"""

from __future__ import annotations

import json
import logging

from openremap.api import errors, registry

log = logging.getLogger(__name__)


def handle_line(raw: str) -> str:
    """Parse one request line and return the response line (JSON string)."""
    req_id: object | None = None
    method: object | None = None

    try:
        req = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return _respond(None, error=_error(errors.PARSE_ERROR, "Parse error"))

    if not isinstance(req, dict):
        return _respond(None, error=_error(errors.INVALID_REQUEST, "Invalid request"))

    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params")
    if params is None:
        params = {}

    if not isinstance(method, str):
        return _respond(
            req_id,
            error=_error(errors.INVALID_REQUEST, "Invalid request: missing method"),
        )
    if not isinstance(params, dict):
        return _respond(
            req_id,
            error=_error(errors.INVALID_PARAMS, "Invalid params: must be an object"),
        )

    try:
        result = registry.dispatch(method, params)
        return _respond(req_id, result=result)
    except errors.GuardError as exc:
        log.warning("guard rejection method=%s id=%s: %s", method, req_id, exc)
        return _respond(req_id, error=_error(exc.code, exc.message))
    except errors.ApiError as exc:
        return _respond(req_id, error=_error(exc.code, exc.message))
    except Exception:
        log.exception("unexpected error method=%s id=%s", method, req_id)
        return _respond(req_id, error=_error(errors.INTERNAL_ERROR, "Internal error"))


def _respond(
    req_id: object,
    *,
    result: object | None = None,
    error: dict | None = None,
) -> str:
    if error is not None:
        return json.dumps({"id": req_id, "error": error})
    return json.dumps({"id": req_id, "result": result})


def _error(code: int, message: str) -> dict:
    return {"code": code, "message": message}
