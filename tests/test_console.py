"""
Tests for ``openremap.console.force_utf8``.

The bug it prevents: on Windows a redirected stream uses the ANSI code page, so
``openremap identify x.bin > out.txt`` raised ``UnicodeEncodeError`` on the first
em dash — and this product's copy is full of them. The function must be a no-op
on a UTF-8 stream and must never raise on a stream it cannot configure.
"""

from __future__ import annotations

import io
import sys

from openremap.console import force_utf8


class _Stream(io.TextIOWrapper):
    """A stream whose encoding we can set, to stand in for a Windows console."""

    def __init__(self, encoding: str) -> None:
        super().__init__(io.BytesIO(), encoding=encoding, errors="strict")


class TestForceUtf8:
    def test_a_utf8_stream_is_left_alone(self, monkeypatch):
        stream = _Stream("utf-8")
        calls: list[dict] = []
        original = stream.reconfigure

        def reconfigure(**kwargs):
            calls.append(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(stream, "reconfigure", reconfigure)
        monkeypatch.setattr(sys, "stdout", stream)
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        force_utf8()
        assert calls == [], "an already-UTF-8 stream must not be reconfigured"

    def test_a_legacy_stream_becomes_utf8_and_never_raises(self, monkeypatch):
        stream = _Stream("cp1252")
        monkeypatch.setattr(sys, "stdout", stream)
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        force_utf8()
        assert stream.encoding.lower().replace("-", "") == "utf8"
        # the point of the change: an em dash now writes instead of raising
        stream.write("→ ✓ —")
        assert stream.errors == "replace"

    def test_a_stream_that_cannot_be_reconfigured_is_skipped(self, monkeypatch):
        class Hostile:
            encoding = "cp1252"

            def reconfigure(self, **kwargs):
                raise ValueError("not mine to configure")

            def write(self, text):
                return len(text)

        monkeypatch.setattr(sys, "stdout", Hostile())
        monkeypatch.setattr(sys, "stderr", Hostile())
        force_utf8()  # must not raise

    def test_an_object_without_reconfigure_is_ignored(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", object())
        monkeypatch.setattr(sys, "stderr", object())
        force_utf8()
