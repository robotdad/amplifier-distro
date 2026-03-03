"""Distro server session directory management.

Creates a structured session directory under ~/.amplifier-distro/sessions/
each time the server starts. The directory contains:

- meta.json  -- session metadata (id, port, pid, start time, config)
- serve.log  -- server stdout/stderr (tee'd alongside console output)

A ``current`` symlink always points to the most recent session.

This structure is designed to be machine-readable so that AI agents and
future debugging tools can discover and monitor running servers.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from amplifier_distro import conventions

logger = logging.getLogger(__name__)


def _session_id() -> str:
    """Generate a UUID4 session ID."""
    return str(uuid.uuid4())


def _get_version() -> str:
    """Return the installed amplifier-distro version."""
    from importlib.metadata import PackageNotFoundError

    try:
        from importlib.metadata import version as pkg_version

        return pkg_version("amplifier-distro")
    except PackageNotFoundError:
        return "unknown"


def create_session_dir(
    *,
    host: str,
    port: int,
    dev_mode: bool,
    stub_mode: bool,
    apps: list[str],
) -> tuple[str, Path]:
    """Create a new session directory and write initial meta.json.

    Returns a (session_id, session_path) tuple.
    """
    session_id = _session_id()
    sessions_root = Path(conventions.DISTRO_SESSIONS_DIR).expanduser()
    session_path = sessions_root / session_id

    session_path.mkdir(parents=True, exist_ok=True)

    # Write meta.json
    meta = {
        "session_id": session_id,
        "pid": os.getpid(),
        "port": port,
        "host": host,
        "start_time": datetime.now(tz=UTC).isoformat(),
        "dev_mode": dev_mode,
        "stub_mode": stub_mode,
        "apps": apps,
        "amplifier_distro_version": _get_version(),
    }
    meta_file = session_path / conventions.DISTRO_SESSION_META_FILENAME
    meta_file.write_text(json.dumps(meta, indent=2) + "\n")

    return session_id, session_path


def setup_session_log(session_path: Path) -> Path:
    """Add a file handler that tees log output to the session's serve.log.

    Returns the log file path.
    """
    log_file = session_path / conventions.DISTRO_SESSION_LOG_FILENAME

    root = logging.getLogger()

    # Use the same human-readable format as the console handler so the
    # session log is easy to read with ``tail -f``.
    handler = logging.FileHandler(str(log_file))
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(handler)

    # Also capture raw stdout/stderr (uvicorn writes directly to these)
    _tee_std_streams(log_file)

    return log_file


class _TeeWriter:
    """Write to both the original stream and a log file."""

    def __init__(self, original: object, log_file: Path) -> None:
        self._original = original
        self._log = open(str(log_file), "a")  # noqa: SIM115

    def write(self, data: str) -> int:
        self._original.write(data)  # type: ignore[union-attr]
        self._log.write(data)
        self._log.flush()
        return len(data)

    def flush(self) -> None:
        self._original.flush()  # type: ignore[union-attr]
        self._log.flush()

    def fileno(self) -> int:
        return self._original.fileno()  # type: ignore[union-attr]

    def isatty(self) -> bool:
        return False


def _tee_std_streams(log_file: Path) -> None:
    """Redirect stdout/stderr to also write to log_file."""
    sys.stdout = _TeeWriter(sys.stdout, log_file)  # type: ignore[assignment]
    sys.stderr = _TeeWriter(sys.stderr, log_file)  # type: ignore[assignment]
