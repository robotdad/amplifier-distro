"""PID-file utilities for the amplifier-distro server."""

from __future__ import annotations

import os
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
