"""
Every key a screen advertises must call something that exists.

Textual resolves ``Binding(action="close")`` to ``action_close()`` and shrugs if
there is no such method: the key silently does nothing, the hint bar still shows
it, and no other test notices. That happened once for real — the recipe detail
advertised ``Esc close`` and pressing it did nothing at all.

So this walks every screen class and checks the wiring, which is cheap and
catches the whole class of mistake (typos included: ``escape`` → ``back`` on a
screen that defines ``action_close``).
"""

from __future__ import annotations

import importlib
import re

import pytest

#: screen module → class name
SCREENS = {
    "apply": "ApplyScreen",
    "batch": "BatchScreen",
    "boot": "BootScreen",
    "cook": "CookScreen",
    "help": "HelpScreen",
    "home": "HomeScreen",
    "identify": "IdentifyScreen",
    "inspect": "InspectScreen",
    "maps": "MapsScreen",
    "picker": "FilePicker",
    "recipe": "RecipeDetailScreen",
}

#: actions Textual provides itself (Screen/App/ModalScreen), so a screen may bind
#: to them without defining anything.
INHERITED = {"quit", "focus_next", "focus_previous", "dismiss", "help_quit"}


def _bindings(module_name: str, class_name: str):
    module = importlib.import_module(f"openremap.tui.screens.{module_name}")
    return getattr(module, class_name)


@pytest.mark.parametrize("module_name", sorted(SCREENS), ids=sorted(SCREENS))
def test_every_binding_resolves_to_a_method(module_name):
    cls = _bindings(module_name, SCREENS[module_name])
    missing = []
    for binding in getattr(cls, "BINDINGS", []):
        # "task('identify')" → "task"; parameters are parsed by Textual
        action = re.split(r"[([]", binding.action, maxsplit=1)[0].strip()
        if action in INHERITED:
            continue
        if not hasattr(cls, f"action_{action}"):
            missing.append(f"{binding.key} → action_{action}")
    assert not missing, f"{cls.__name__} advertises keys that do nothing: {missing}"


@pytest.mark.parametrize("module_name", sorted(SCREENS), ids=sorted(SCREENS))
def test_no_binding_uses_a_shift_prefix(module_name):
    """A terminal sends "R" for Shift+R, never "shift+r".

    A binding written as ``"shift+r"`` therefore fires in a test harness that
    presses that exact key name and *never* for a human. One shipped like that
    (the recent-list clear) and its test happily passed — so the rule is pinned.
    """
    cls = _bindings(module_name, SCREENS[module_name])
    keys = {binding.key for binding in getattr(cls, "BINDINGS", [])}
    offenders = [
        key
        for key in keys
        if key.startswith("shift+")
        and len(key) == 7
        and key[-1].upper() not in keys  # the character form must exist as well
    ]
    assert not offenders, (
        f"{cls.__name__} binds {offenders} without the character form; a terminal "
        'sends "R" for Shift+R, so a shift+ binding alone never fires'
    )


@pytest.mark.parametrize("module_name", sorted(SCREENS), ids=sorted(SCREENS))
def test_no_binding_is_declared_twice(module_name):
    """Two bindings on one key means the second silently never fires."""
    cls = _bindings(module_name, SCREENS[module_name])
    keys = [binding.key for binding in getattr(cls, "BINDINGS", [])]
    duplicates = {key for key in keys if keys.count(key) > 1}
    assert not duplicates, f"{cls.__name__} binds {duplicates} twice"
