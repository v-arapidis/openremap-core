"""
Phase 1 contract tests — the API foundation.

Covers: registry dispatch, the single serialization point, the error taxonomy,
and the JSON-RPC stdio transport end-to-end (in-process, no subprocess).
"""

import json
from io import StringIO

import pytest

import openremap.api as api
from openremap.api import errors
from openremap.api.transport import jsonrpc, stdio


# --- in-process dispatch -----------------------------------------------------


def test_ping_in_process():
    assert api.call("ping") == {"ok": True}


def test_ping_direct_function():
    assert api.ping() == {"ok": True}


def test_version_in_process():
    result = api.call("version")
    assert "version" in result
    assert "backend" in result


def test_list_methods_lists_ping_and_version():
    names = {m["name"] for m in api.list_methods()}
    assert {"ping", "version"} <= names


def test_list_methods_filters_by_kind():
    commands = api.list_methods(kind="command")
    assert all(m["kind"] == "command" for m in commands)


def test_unknown_method_raises():
    with pytest.raises(errors.UnknownMethodError):
        api.call("does_not_exist")


def test_list_methods_in_process():
    names = {m["name"] for m in api.call("list_methods")["methods"]}
    assert {"ping", "version", "list_methods"} <= names


def test_list_methods_over_rpc():
    line = jsonrpc.handle_line('{"id": 5, "method": "list_methods", "params": {}}')
    resp = json.loads(line)
    names = {m["name"] for m in resp["result"]["methods"]}
    assert {"ping", "version", "list_methods"} <= names


# --- JSON-RPC framing + error mapping ---------------------------------------


def test_jsonrpc_ping_roundtrip():
    line = jsonrpc.handle_line('{"id": 1, "method": "ping", "params": {}}')
    assert json.loads(line) == {"id": 1, "result": {"ok": True}}


def test_jsonrpc_unknown_method():
    line = jsonrpc.handle_line('{"id": 7, "method": "nope", "params": {}}')
    resp = json.loads(line)
    assert resp["id"] == 7
    assert resp["error"] == {
        "code": errors.METHOD_NOT_FOUND,
        "message": "Unknown method: 'nope'",
    }


def test_jsonrpc_parse_error():
    line = jsonrpc.handle_line("{not json")
    resp = json.loads(line)
    assert resp["error"]["code"] == errors.PARSE_ERROR


def test_jsonrpc_invalid_params_not_object():
    line = jsonrpc.handle_line('{"id": 2, "method": "ping", "params": []}')
    resp = json.loads(line)
    assert resp["error"]["code"] == errors.INVALID_PARAMS


def test_guard_error_maps_to_clean_error():
    name = "_test_guard"

    def _raise_guard(params: dict) -> dict:
        raise errors.GuardError("non-unique anchors")

    try:
        api.registry.register(name, kind="command", description="test")(
            _raise_guard
        )
        line = jsonrpc.handle_line(f'{{"id": 9, "method": "{name}", "params": {{}}}}')
        resp = json.loads(line)
        assert resp["error"] == {
            "code": errors.GUARD_REJECTED,
            "message": "non-unique anchors",
        }
    finally:
        api.registry.unregister(name)


# --- stdio loop (in-process, via StringIO) -----------------------------------


def test_stdio_loop_end_to_end():
    inp = StringIO(
        '{"id": 1, "method": "ping", "params": {}}\n'
        '{"id": 2, "method": "version", "params": {}}\n'
    )
    out = StringIO()
    stdio.serve(inp, out)
    lines = out.getvalue().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"id": 1, "result": {"ok": True}}
    assert "version" in json.loads(lines[1])["result"]


def test_stdio_subprocess_roundtrip():
    """The ``python -m openremap.api.transport.stdio`` entry point round-trips.

    Guards the Phase 4 cutover: the old ``openremap/server.py`` daemon is gone
    and this module is the replacement console entry point.
    """
    import subprocess
    import sys

    proc = subprocess.Popen(
        [sys.executable, "-m", "openremap.api.transport.stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        proc.stdin.write('{"id": 1, "method": "ping", "params": {}}\n')
        proc.stdin.write('{"id": 2, "method": "list_methods", "params": {}}\n')
        proc.stdin.write('{"id": 3, "method": "nope", "params": {}}\n')
        proc.stdin.flush()

        lines = [proc.stdout.readline() for _ in range(3)]
        assert json.loads(lines[0]) == {"id": 1, "result": {"ok": True}}
        names = {m["name"] for m in json.loads(lines[1])["result"]["methods"]}
        assert {"ping", "list_methods"} <= names
        err = json.loads(lines[2])
        assert err["error"]["code"] == errors.METHOD_NOT_FOUND
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
