"""
Contract tests for the ``generate_dump`` API method.

The method exists so a host can create its own fixtures over RPC without
shipping anyone's ROM data.  These tests pin the wire contract: the schema,
the base64 payload, the ground-truth shape, determinism, and the error
taxonomy (bad parameters must be a clean ``InvalidParamsError``, not a crash).
"""

from __future__ import annotations

import base64
import json

import pytest

import openremap.api as api
from openremap.api import errors
from openremap.api.transport import jsonrpc


def test_method_is_registered_with_a_schema():
    meta = next(m for m in api.list_methods() if m["name"] == "generate_dump")
    assert meta["kind"] == "command"
    schema = meta["input_schema"]
    assert schema["size"]["default"] == 262144
    assert schema["maps"]["default"] == 1
    assert schema["cell_width"]["min"] == 1
    assert schema["byte_order"]["default"] == "little"


def test_call_returns_base64_bytes_and_ground_truth():
    result = api.call("generate_dump", {"size": 65536, "maps": 2, "seed": 3})

    assert result["size"] == 65536
    assert len(base64.b64decode(result["data_b64"])) == 65536
    assert len(result["sha256"]) == 64
    assert [s["kind"] for s in result["sectors"]] == [
        "code",
        "calibration",
        "erased",
        "code",
    ]
    assert len(result["maps"]) == 2
    for m in result["maps"]:
        assert m["cols"] > 0 and m["rows"] > 0
        assert m["cell_width"] in (1, 2)
        assert m["x_axis_offset"] < m["y_axis_offset"] < m["offset"] < m["end"]
        assert len(m["x_axis"]) == m["cols"]
        assert len(m["y_axis"]) == m["rows"]
        assert "cells" not in m, "cell values are not part of the wire payload"


def test_same_parameters_give_identical_bytes():
    first = api.call("generate_dump", {"size": 32768, "maps": 1, "seed": 5})
    second = api.call("generate_dump", {"size": 32768, "maps": 1, "seed": 5})
    assert first == second


def test_defaults_apply_when_params_are_omitted():
    result = api.call("generate_dump")
    assert result["size"] == 262144
    assert result["params"]["seed"] == 7
    assert len(result["maps"]) == 1


def test_params_are_echoed_for_regeneration():
    result = api.call(
        "generate_dump",
        {"size": 49152, "maps": 3, "seed": 42, "cell_width": 1, "byte_order": "big"},
    )
    assert result["params"] == {
        "size": 49152,
        "maps": 3,
        "seed": 42,
        "layout": ["code", "calibration", "erased", "code"],
        "cell_width": 1,
        "byte_order": "big",
    }


def test_generated_file_identifies_as_unknown():
    """A synthetic dump must not be mistaken for a real family."""
    from openremap.core.services.identify.identifier import identify_ecu

    result = api.call("generate_dump", {"size": 65536, "maps": 2})
    data = base64.b64decode(result["data_b64"])
    identity = identify_ecu(data, "synthetic.bin")
    assert identity["manufacturer"] is None
    assert identity["ecu_family"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"layout": ["code", "nonsense"]},
        {"layout": []},
        {"maps": -1},
        {"cell_width": 3},
        {"byte_order": "middle"},
    ],
)
def test_bad_parameters_are_invalid_params(params):
    with pytest.raises(errors.InvalidParamsError):
        api.call("generate_dump", params)


def test_maps_that_do_not_fit_are_invalid_params():
    with pytest.raises(errors.InvalidParamsError, match="too small"):
        api.call("generate_dump", {"size": 8192, "maps": 40, "layout": ["calibration"]})


def test_bad_parameters_map_to_a_jsonrpc_error():
    line = jsonrpc.handle_line(
        '{"id": 4, "method": "generate_dump", "params": {"layout": ["nope"]}}'
    )
    resp = json.loads(line)
    assert resp["id"] == 4
    assert resp["error"]["code"] == errors.INVALID_PARAMS
    assert "generate_dump" in resp["error"]["message"]
