"""
openremap.api.errors — the typed error taxonomy for the API layer.

The API raises these; the transport maps them to JSON-RPC error objects.
Keeping them separate from the transport means domain logic stays
transport-agnostic: in-process callers get Python exceptions, RPC clients get
``{"code": ..., "message": ...}`` dicts.
"""

from __future__ import annotations

# JSON-RPC 2.0 standard error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Application-defined range (-32000 … -32099).
GUARD_REJECTED = -32000


class ApiError(Exception):
    """Base class for every API-layer error."""

    code: int = INTERNAL_ERROR

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class UnknownMethodError(ApiError):
    """Requested method is not registered."""

    code = METHOD_NOT_FOUND


class InvalidParamsError(ApiError):
    """Params failed schema validation (missing / wrong type / out of range)."""

    code = INVALID_PARAMS


class GuardError(ApiError):
    """Expected business-rule rejection (non-unique anchors, size mismatch…).

    Not a bug: mapped to a clean client-facing error, never a traceback.
    """

    code = GUARD_REJECTED


class InternalError(ApiError):
    """Unexpected internal failure (the transport maps bare exceptions here)."""

    code = INTERNAL_ERROR
