"""
orjson integration parity tests.

The recipe-load paths (tune / validate / merge / audit / diff-maps / TUI)
parse JSON with orjson while serialization stays on the stdlib.  These
tests pin the two contracts that swap relies on:

1. `orjson.loads` parses every payload the stdlib `json.dumps` produces
   into the exact same Python object (recipe dicts, JSON reports).
2. `orjson.JSONDecodeError` is a subclass of `json.JSONDecodeError`, so
   the existing `except json.JSONDecodeError` handlers in the CLI keep
   catching malformed-recipe errors unchanged.
"""

import json

import orjson
import pytest

from tests.conftest import make_instruction, make_recipe


def _recipe_like_payload() -> dict:
    """A representative recipe dict: nested dicts, lists, floats, unicode."""
    inst = make_instruction(0x1234, "AABB", "CCDD", ctx="00010203")
    inst.update(
        {
            "ctx_entropy": 2.584962500721156,  # float — Shannon entropy
            "ctx_unique": True,
            "ctx_expanded": False,
        }
    )
    recipe = make_recipe([inst])
    recipe["ecu"] = {
        "manufacturer": "Bosch",
        "hw_number": "0261207788",
        "sw_version": "0040000",
        # unicode — stdlib dumps with ensure_ascii=False, orjson emits UTF-8
        "label": "GmbH & Co. KG — Ünïcode ✓",
    }
    recipe["statistics"] = {"changed_bytes": 3, "correlation": 0.987654321}
    return recipe


def test_orjson_loads_matches_stdlib_for_dumps_output():
    """orjson.loads(json.dumps(x)) == json.loads(json.dumps(x))."""
    payload = _recipe_like_payload()
    blob = json.dumps(payload, ensure_ascii=False)
    assert orjson.loads(blob) == json.loads(blob)


def test_orjson_loads_accepts_str_and_bytes():
    payload = _recipe_like_payload()
    blob = json.dumps(payload, ensure_ascii=False)
    assert orjson.loads(blob) == payload
    assert orjson.loads(blob.encode("utf-8")) == payload


def test_orjson_json_decode_error_subclasses_stdlib():
    """The CLI handlers rely on `except json.JSONDecodeError` catching orjson errors."""
    assert issubclass(orjson.JSONDecodeError, json.JSONDecodeError)
    with pytest.raises(json.JSONDecodeError):
        orjson.loads("{not valid json")


def test_orjson_handles_non_finite_floats_differently():
    """
    Document the deliberate serializer difference (why dumps stays stdlib):
    stdlib emits Infinity (invalid JSON), orjson emits null.  The diff-maps
    output path sanitizes inf to the string "inf" before serializing.
    """
    assert json.loads(json.dumps(float("inf"))) == float("inf")
    assert orjson.loads(orjson.dumps(float("inf"))) is None
    # Sanitized form — what diff-maps actually writes — round-trips in both.
    sanitized = {"avg_pct": "inf"}
    assert orjson.loads(json.dumps(sanitized)) == sanitized
    assert orjson.loads(json.dumps(sanitized)) == json.loads(json.dumps(sanitized))


def test_orjson_loads_rejects_nonfinite_literals_stdlib_accepts():
    """
    Spec-strictness note: stdlib silently accepts the non-standard
    `Infinity` literal; orjson rejects it.  Our own writers never emit it
    (inf is sanitized to the string "inf"), so recipe reads are unaffected.
    """
    with pytest.raises(json.JSONDecodeError):
        orjson.loads('{"x": Infinity}')
    assert json.loads('{"x": Infinity}') == {"x": float("inf")}
