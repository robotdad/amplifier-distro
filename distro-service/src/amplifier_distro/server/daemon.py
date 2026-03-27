"""PID-file utilities for the amplifier-distro server."""

from __future__ import annotations

import os
import socket
from pathlib import Path


def read_pid(pid_path: Path) -> int | None:
    """Read an integer PID from a .pid file. Returns None on any error."""
    try:
        return int(pid_path.read_text().strip())
    except (OSError, ValueError):
        return None


def is_running(pid_path: Path) -> bool:
    """Return True if the PID in the file corresponds to a live process."""
    pid = read_pid(pid_path)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)  # signal 0 = existence check, no signal sent
        return True
    except (ProcessLookupError, PermissionError):
        return False


def write_pid(pid_path: Path, pid: int | None = None) -> None:
    """Write the current (or given) PID to a .pid file."""
    if pid is None:
        pid = os.getpid()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(pid))


def remove_pid(pid_path: Path) -> None:
    """Remove a PID file if it exists."""
    try:
        pid_path.unlink()
    except FileNotFoundError:
        pass


def check_port(host: str, port: int) -> bool:
    """Return True if the port is available for binding.

    Probes using the same address family that the server will use.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            return True
    except OSError:
        return False
