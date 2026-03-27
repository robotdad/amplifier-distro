"""Tests for PID-file and port utilities."""

from __future__ import annotations

import socket
from pathlib import Path

from amplifier_distro.server.daemon import (
    check_port,
    is_running,
    read_pid,
    remove_pid,
    write_pid,
)


class TestReadPid:
    def test_returns_pid_from_file(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("12345")
        assert read_pid(pid_file) == 12345

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        assert read_pid(tmp_path / "missing.pid") is None

    def test_returns_none_for_non_integer(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "bad.pid"
        pid_file.write_text("not-a-number")
        assert read_pid(pid_file) is None


class TestIsRunning:
    def test_returns_false_for_missing_file(self, tmp_path: Path) -> None:
        assert is_running(tmp_path / "missing.pid") is False

    def test_returns_true_for_current_process(self, tmp_path: Path) -> None:
        import os

        pid_file = tmp_path / "self.pid"
        pid_file.write_text(str(os.getpid()))
        assert is_running(pid_file) is True

    def test_returns_false_for_dead_process(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "dead.pid"
        pid_file.write_text("99999999")  # Very unlikely to be a real PID
        assert is_running(pid_file) is False


class TestWritePid:
    def test_writes_current_pid(self, tmp_path: Path) -> None:
        import os

        pid_file = tmp_path / "server.pid"
        write_pid(pid_file)
        assert pid_file.read_text().strip() == str(os.getpid())

    def test_writes_explicit_pid(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "server.pid"
        write_pid(pid_file, pid=42)
        assert pid_file.read_text().strip() == "42"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "deep" / "nested" / "server.pid"
        write_pid(pid_file, pid=1)
        assert pid_file.exists()


class TestRemovePid:
    def test_removes_existing_file(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "server.pid"
        pid_file.write_text("123")
        remove_pid(pid_file)
        assert not pid_file.exists()

    def test_noop_for_missing_file(self, tmp_path: Path) -> None:
        # Should not raise
        remove_pid(tmp_path / "missing.pid")


class TestCheckPort:
    def test_available_port_returns_true(self) -> None:
        # Use port 0 to find a free port, then check it
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            _, free_port = s.getsockname()
        # Port is now free (socket closed)
        assert check_port("127.0.0.1", free_port) is True

    def test_occupied_port_returns_false(self) -> None:
        # Bind a port and keep it held
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", 0))
            _, held_port = s.getsockname()
            s.listen(1)
            # Port is occupied — check_port should return False
            assert check_port("127.0.0.1", held_port) is False
