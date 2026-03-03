"""Pin storage -- manages pinned session IDs in ~/.amplifier/pinned-sessions.json.

Pinned sessions are a UI preference stored separately from session metadata.
The file is a JSON object with:
    pinned: list[str]           -- ordered list of pinned session IDs
    pinned_at: dict[str, str]   -- ISO timestamps of when each session was pinned
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from amplifier_distro.conventions import AMPLIFIER_HOME

logger = logging.getLogger(__name__)

_AMPLIFIER_HOME_OVERRIDE: str | None = None  # Overridable in tests
_PIN_FILENAME = "pinned-sessions.json"


def _get_amplifier_home() -> str:
    return (
        _AMPLIFIER_HOME_OVERRIDE
        if _AMPLIFIER_HOME_OVERRIDE is not None
        else AMPLIFIER_HOME
    )


def _pin_file_path() -> Path:
    return Path(_get_amplifier_home()).expanduser() / _PIN_FILENAME


def _read_pin_data() -> dict[str, Any]:
    """Read pin data from disk. Returns empty structure on any error."""
    path = _pin_file_path()
    if not path.exists():
        return {"pinned": [], "pinned_at": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("pinned"), list):
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        logger.warning("Could not read pin file at %s", path, exc_info=True)
    return {"pinned": [], "pinned_at": {}}


def _write_pin_data(data: dict[str, Any]) -> None:
    """Write pin data to disk. Creates parent directories if needed."""
    path = _pin_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_pins() -> set[str]:
    """Return the set of currently pinned session IDs."""
    data = _read_pin_data()
    return {sid for sid in data["pinned"] if isinstance(sid, str)}


def get_pins_with_timestamps() -> dict[str, str]:
    """Return pinned session IDs mapped to their pin timestamps."""
    data = _read_pin_data()
    pinned_at = data.get("pinned_at", {})
    return {
        sid: pinned_at.get(sid, "") for sid in data["pinned"] if isinstance(sid, str)
    }


def add_pin(session_id: str) -> None:
    """Pin a session. Idempotent -- no-op if already pinned."""
    data = _read_pin_data()
    pinned = data["pinned"]
    if session_id in pinned:
        return
    pinned.append(session_id)
    pinned_at = data.get("pinned_at", {})
    pinned_at[session_id] = datetime.now(UTC).isoformat()
    data["pinned_at"] = pinned_at
    _write_pin_data(data)


def remove_pin(session_id: str) -> None:
    """Unpin a session. No-op if not currently pinned."""
    data = _read_pin_data()
    pinned = data["pinned"]
    if session_id not in pinned:
        return
    pinned.remove(session_id)
    pinned_at = data.get("pinned_at", {})
    pinned_at.pop(session_id, None)
    data["pinned_at"] = pinned_at
    _write_pin_data(data)
