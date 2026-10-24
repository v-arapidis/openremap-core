"""Tests for the declarative workflow engine (kind="workflow")."""

from __future__ import annotations

import os

import pytest

import openremap.api as api
from openremap.api import errors
from openremap.api.workflows import load_workflows


def _write_workflow(tmp_path, text: str):
    p = tmp_path / "wf.toml"
    p.write_text(text)
    return p


def _register_echo():
    """Register a tiny echo command used to exercise $prev/$as plumbing."""
    api.registry.register("_wf_echo", kind="command", description="test echo")(
        lambda params: {"value": params}
    )


def _cleanup(*names):
    for name in names:
        api.registry.unregister(name)


class TestBuiltinWorkflow:
    def test_identify_cook_is_registered(self):
        wfs = api.list_methods(kind="workflow")
        names = {w["name"] for w in wfs}
        assert "identify_cook" in names
        wf = next(w for w in wfs if w["name"] == "identify_cook")
        assert wf["steps"] == ["identify", "identify", "cook_strict"]
        assert set(wf["input_schema"]) == {"original_path", "modified_path"}

    def test_identify_cook_runs(self, tmp_path):
        orig = tmp_path / "stock.bin"
        mod = tmp_path / "tuned.bin"
        stock = bytearray(os.urandom(8192))
        tuned = bytearray(stock)
        tuned[100] ^= 0xFF
        orig.write_bytes(bytes(stock))
        mod.write_bytes(bytes(tuned))

        r = api.call(
            "identify_cook",
            {"original_path": str(orig), "modified_path": str(mod)},
        )
        assert "recipe" in r
        assert r["recipe"]["instructions"]


class TestPlumbing:
    def test_input_and_as_and_prev_resolution(self, tmp_path):
        _register_echo()
        _write_workflow(
            tmp_path,
            """
name = "test_plumb"

[[steps]]
call = "_wf_echo"
params = { x = "$input.a" }
as = "first"

[[steps]]
call = "_wf_echo"
params = { prev = "$prev.value", first = "$first.value" }
""",
        )
        load_workflows([tmp_path])
        try:
            r = api.call("test_plumb", {"a": 123})
            assert r["value"]["prev"] == {"x": 123}
            assert r["value"]["first"] == {"x": 123}
        finally:
            _cleanup("test_plumb", "_wf_echo")

    def test_output_selects_bound_step(self, tmp_path):
        _register_echo()
        _write_workflow(
            tmp_path,
            """
name = "test_output"
output = "first"

[[steps]]
call = "_wf_echo"
params = { a = 1 }
as = "first"

[[steps]]
call = "_wf_echo"
params = { a = 2 }
as = "second"
""",
        )
        load_workflows([tmp_path])
        try:
            assert api.call("test_output", {}) == {"value": {"a": 1}}
        finally:
            _cleanup("test_output", "_wf_echo")

    def test_embedded_string_substitution(self, tmp_path):
        _register_echo()
        _write_workflow(
            tmp_path,
            """
name = "test_embed"

[[steps]]
call = "_wf_echo"
params = { msg = "hello $input.name!" }
""",
        )
        load_workflows([tmp_path])
        try:
            assert api.call("test_embed", {"name": "world"})["value"]["msg"] == (
                "hello world!"
            )
        finally:
            _cleanup("test_embed", "_wf_echo")

    def test_nested_dict_and_list_resolution(self, tmp_path):
        _register_echo()
        _write_workflow(
            tmp_path,
            """
name = "test_nested"

[[steps]]
call = "_wf_echo"
params = { nested = { key = "$input.x" }, lst = ["$input.x", 7] }
""",
        )
        load_workflows([tmp_path])
        try:
            r = api.call("test_nested", {"x": "val"})
            assert r["value"]["nested"] == {"key": "val"}
            assert r["value"]["lst"] == ["val", 7]
        finally:
            _cleanup("test_nested", "_wf_echo")


class TestErrors:
    def test_missing_input_raises_invalid_params(self):
        with pytest.raises(errors.InvalidParamsError):
            api.call("identify_cook", {})

    def test_reserved_as_name_skipped_not_registered(self, tmp_path):
        # Guardrail intact: reserved 'as' names are rejected — in a user dir
        # the file is skipped with a warning instead of raising (0.7.6 fix).
        _write_workflow(
            tmp_path,
            """
name = "test_reserved"

[[steps]]
call = "ping"
as = "input"
""",
        )
        with pytest.warns(UserWarning, match="skipping"):
            names = load_workflows([tmp_path])
        assert "test_reserved" not in names
        assert api.registry.get_method("test_reserved") is None

    def test_missing_steps_skipped_not_registered(self, tmp_path):
        _write_workflow(tmp_path, 'name = "x"\n')
        with pytest.warns(UserWarning, match="skipping"):
            names = load_workflows([tmp_path])
        assert "x" not in names
        assert api.registry.get_method("x") is None

    def test_unknown_step_method_raises(self, tmp_path):
        _write_workflow(
            tmp_path,
            """
name = "test_unknown"

[[steps]]
call = "does_not_exist"
""",
        )
        load_workflows([tmp_path])
        try:
            with pytest.raises(errors.UnknownMethodError):
                api.call("test_unknown", {})
        finally:
            _cleanup("test_unknown")


class TestUserDirTolerance:
    """One bad third-party ``.toml`` must not take down loading (or import).

    Built-in workflows fail fast (a broken shipped workflow is a bug);
    user-supplied dirs — explicit ``load_workflows([dir])`` or
    ``OPENREMAP_WORKFLOW_DIRS`` — skip malformed/invalid files with a warning.
    """

    def test_malformed_toml_skipped_with_warning(self, tmp_path):
        (tmp_path / "broken.toml").write_text("name = 'broken'\nsteps = [\n")
        with pytest.warns(UserWarning, match="skipping"):
            names = load_workflows([tmp_path])
        assert "broken" not in names
        assert api.registry.get_method("broken") is None

    def test_good_sibling_still_loads(self, tmp_path):
        (tmp_path / "broken.toml").write_text("name = 'broken'\nsteps = [\n")
        (tmp_path / "good.toml").write_text(
            'name = "sibling_ok"\n\n[[steps]]\ncall = "ping"\n'
        )
        with pytest.warns(UserWarning, match="skipping"):
            names = load_workflows([tmp_path])
        try:
            assert "sibling_ok" in names
            assert api.registry.get_method("sibling_ok") is not None
        finally:
            api.registry.unregister("sibling_ok")

    def test_env_dir_bad_file_skipped(self, tmp_path, monkeypatch):
        # The audit repro: a bad .toml behind OPENREMAP_WORKFLOW_DIRS must not
        # crash the load that also runs at ``import openremap.api``.
        (tmp_path / "broken.toml").write_text("name = 'broken'\nsteps = [\n")
        monkeypatch.setenv("OPENREMAP_WORKFLOW_DIRS", str(tmp_path))
        with pytest.warns(UserWarning, match="skipping"):
            names = load_workflows()
        assert "broken" not in names
        assert "identify_cook" in names  # built-ins still loaded

    def test_builtin_dir_fails_fast(self, tmp_path, monkeypatch):
        (tmp_path / "bad.toml").write_text("name = 'bad'\nsteps = [\n")
        monkeypatch.delenv("OPENREMAP_WORKFLOW_DIRS", raising=False)
        import openremap.api.workflows as workflows_mod

        monkeypatch.setattr(workflows_mod, "BUILTIN_DIR", tmp_path)
        with pytest.raises(ValueError):
            load_workflows()
