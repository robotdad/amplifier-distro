"""Tests for pin_storage module."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point pin_storage at a temp directory."""
    import amplifier_distro.server.apps.chat.pin_storage as pin_mod

    monkeypatch.setattr(pin_mod, "_AMPLIFIER_HOME_OVERRIDE", str(tmp_path))
    return tmp_path


class TestPinStorage:
    def test_load_pins_returns_empty_set_when_no_file(self, tmp_home: Path) -> None:
        from amplifier_distro.server.apps.chat.pin_storage import load_pins

        assert load_pins() == set()

    def test_add_pin_creates_file_and_persists(self, tmp_home: Path) -> None:
        from amplifier_distro.server.apps.chat.pin_storage import add_pin, load_pins

        add_pin("session-abc")
        assert "session-abc" in load_pins()
        # Verify file exists on disk
        pin_file = tmp_home / "pinned-sessions.json"
        assert pin_file.exists()

    def test_add_pin_is_idempotent(self, tmp_home: Path) -> None:
        from amplifier_distro.server.apps.chat.pin_storage import add_pin, load_pins

        add_pin("session-abc")
        add_pin("session-abc")
        pins = load_pins()
        assert pins == {"session-abc"}

    def test_remove_pin_removes_from_set(self, tmp_home: Path) -> None:
        from amplifier_distro.server.apps.chat.pin_storage import (
            add_pin,
            load_pins,
            remove_pin,
        )

        add_pin("session-abc")
        add_pin("session-def")
        remove_pin("session-abc")
        assert load_pins() == {"session-def"}

    def test_remove_pin_noop_when_not_pinned(self, tmp_home: Path) -> None:
        from amplifier_distro.server.apps.chat.pin_storage import load_pins, remove_pin

        remove_pin("nonexistent")
        assert load_pins() == set()

    def test_load_pins_handles_corrupted_file(self, tmp_home: Path) -> None:
        from amplifier_distro.server.apps.chat.pin_storage import load_pins

        pin_file = tmp_home / "pinned-sessions.json"
        pin_file.write_text("not valid json", encoding="utf-8")
        assert load_pins() == set()

    def test_add_pin_records_timestamp(self, tmp_home: Path) -> None:
        from amplifier_distro.server.apps.chat.pin_storage import (
            add_pin,
            get_pins_with_timestamps,
        )

        add_pin("session-abc")
        pins_ts = get_pins_with_timestamps()
        assert "session-abc" in pins_ts
        assert isinstance(pins_ts["session-abc"], str)  # ISO timestamp

    def test_multiple_pins_preserved_across_reads(self, tmp_home: Path) -> None:
        from amplifier_distro.server.apps.chat.pin_storage import add_pin, load_pins

        add_pin("session-1")
        add_pin("session-2")
        add_pin("session-3")
        assert load_pins() == {"session-1", "session-2", "session-3"}