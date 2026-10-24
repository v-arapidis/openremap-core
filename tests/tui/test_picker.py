"""
Tests for the in-terminal file picker.

Two things this screen must get right, both of them learned the hard way: the
path field must never fail silently (a typo used to leave the tree exactly where
it was, with no message at all), and a Windows drive must be reachable by typing
it — ``D:`` is drive-relative there and does not mean the drive root.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openremap.tui.app import OpenRemapTUI
from openremap.tui.screens.picker import (
    FilePicker,
    _typed_path,
    available_drives,
    normalize_typed,
)


def run(body, *, size=(100, 30)):
    async def scenario():
        app = OpenRemapTUI()
        app.fast_boot = True
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.05)
            return await body(pilot, app)

    return asyncio.run(scenario())


class TestTheTypedPathRule:
    """Asserted on the *rule* (pure text), never on a rendered path.

    Both earlier versions compared ``str(_typed_path(...))``, which on Windows runs
    through ``WindowsPath`` — and pathlib appends a root separator to a UNC drive,
    while on POSIX the same text is just a filename. The test measured the
    runner's flavour instead of the rule: green here, red on windows-latest, twice.
    """

    def test_a_bare_drive_becomes_the_drive_root_on_windows(self):
        """A bare drive letter is drive-relative on Windows, and typing it means the root."""
        assert normalize_typed("D:", windows=True) == "D:\\"
        assert normalize_typed("d:", windows=True) == "d:\\"

    def test_a_drive_with_a_root_is_left_alone(self):
        assert normalize_typed("D:\\", windows=True) == "D:\\"
        assert normalize_typed("D:\\dumps\\a.bin", windows=True) == "D:\\dumps\\a.bin"

    def test_a_unc_share_is_left_alone(self):
        assert normalize_typed("\\\\server\\share", windows=True) == "\\\\server\\share"

    def test_only_a_bare_drive_spec_matches(self):
        """A drive-relative path with a name is not bare, so it is left alone."""
        assert normalize_typed("D:stuff", windows=True) == "D:stuff"
        assert normalize_typed("file.bin", windows=True) == "file.bin"

    def test_posix_text_is_never_rewritten(self):
        """On POSIX the same text is a filename, so the Windows rule must not run."""
        assert normalize_typed("D:", windows=False) == "D:"

    def test_whitespace_is_trimmed(self):
        assert normalize_typed("  D:  ", windows=True) == "D:\\"


class TestThePathWrapper:
    """What the wrapper adds on top of the rule: a real ``Path``, ``~`` expanded."""

    def test_home_expands(self):
        expanded = _typed_path("~/dumps")
        assert "~" not in str(expanded)
        assert expanded.parts[-1] == "dumps"

    def test_a_bare_drive_still_becomes_a_path(self):
        assert isinstance(_typed_path("D:", windows=True), Path)


class TestPathField:
    def test_a_wrong_path_says_so_and_stays_put(self, tmp_path):
        async def body(pilot, app):
            picker = FilePicker(tmp_path)
            app.push_screen(picker)
            await pilot.pause(0.3)
            messages: list[str] = []
            picker.notify = lambda message, **kwargs: messages.append(message)
            tree = picker.query_one("#picker-tree")
            before = tree.path
            field = picker.query_one("#picker-path")
            field.focus()
            field.insert_text_at_cursor(str(tmp_path / "does-not-exist"))
            await pilot.press("enter")
            await pilot.pause(0.3)
            return before, tree.path, messages, picker.is_attached

        before, after, messages, attached = run(body)
        assert after == before, "the tree must not move on a path that is not there"
        assert messages and "no such folder" in messages[0]
        assert attached is True, "a typo must not close the picker either"

    def test_a_real_folder_navigates_the_tree(self, tmp_path):
        target = tmp_path / "dumps"
        target.mkdir()

        async def body(pilot, app):
            picker = FilePicker(tmp_path)
            app.push_screen(picker)
            await pilot.pause(0.3)
            field = picker.query_one("#picker-path")
            field.focus()
            field.insert_text_at_cursor(str(target))
            await pilot.press("enter")
            await pilot.pause(0.3)
            return picker.query_one("#picker-tree").path

        assert run(body) == target.resolve()


class TestDrives:
    def test_no_drive_list_off_windows(self):
        """Drive letters are a Windows concept; the row must not be noise elsewhere."""
        assert available_drives(windows=False) == []

    def test_existing_drives_are_listed(self):
        drives = available_drives(windows=True, exists=lambda p: p[0] in "CD")
        assert drives == ["C:\\", "D:\\"]

    @pytest.mark.parametrize("drives,expected_shown", [([], False), (["C:\\", "D:\\"], True)])
    def test_the_row_follows_what_the_platform_reports(
        self, tmp_path, monkeypatch, drives, expected_shown
    ):
        """Both directions, forced — not "whatever this machine has".

        The first version asserted the row was hidden, which is true on POSIX and
        false on windows-latest: it tested the runner, not the behaviour (caught by
        the portability job).
        """
        monkeypatch.setattr(
            "openremap.tui.screens.picker.available_drives", lambda **kwargs: drives
        )

        async def body(pilot, app):
            picker = FilePicker(tmp_path)
            app.push_screen(picker)
            await pilot.pause(0.3)
            row = picker.query_one("#picker-drives")
            return row.display, str(row.render())

        shown, text = run(body)
        assert shown is expected_shown
        if expected_shown:
            assert "C:\\" in text and "type one to go there" in text
        else:
            assert text == ""
