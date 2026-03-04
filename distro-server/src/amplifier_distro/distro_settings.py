"""Centralized distro settings - replaces the old distro.yaml.

Provides a typed, dataclass-based schema for all distro configuration
that isn't secrets (those stay in keys.env) or Amplifier-foundation
config (that stays in settings.yaml).

Settings file: ``~/.amplifier-distro/settings.yaml`` (or DISTRO_HOME-relative).

Usage::

    from amplifier_distro.distro_settings import load, save, DistroSettings

    settings = load()
    settings.slack.hub_channel_name = "my-channel"
    save(settings)
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from amplifier_distro import conventions

logger = logging.getLogger(__name__)

# Guards the load/modify/save cycle so concurrent API requests
# (settings page, install wizard, Slack setup) cannot clobber each other.
_settings_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@dataclass
class IdentitySettings:
    """User identity (persisted from wizard detection)."""

    github_handle: str = ""
    git_email: str = ""


@dataclass
class BackupSettings:
    """GitHub backup repository configuration."""

    repo_name: str = "amplifier-backup"
    repo_owner: str = ""  # empty = same as github_handle


@dataclass
class SlackSettings:
    """Slack bridge non-secret configuration."""

    hub_channel_id: str = ""
    hub_channel_name: str = "amplifier"
    socket_mode: bool = False
    default_working_dir: str = "~"
    simulator_mode: bool = False
    thread_per_session: bool = True
    allow_breakout: bool = True
    channel_prefix: str = "amp-"
    bot_name: str = "slackbridge"
    default_bundle: str = ""
    max_message_length: int = 3900
    response_timeout: int = 300


@dataclass
class VoiceSettings:
    """Voice bridge configuration."""

    voice: str = "ash"
    model: str = "gpt-4o-realtime-preview"
    instructions: str = ""
    tools_enabled: bool = False
    # Wake word prefix and TTS persona name
    assistant_name: str = "Amplifier"


@dataclass
class WatchdogSettings:
    """Watchdog timing configuration."""

    check_interval: int = 30  # seconds between health checks
    restart_after: int = 300  # seconds of downtime before restart
    max_restarts: int = 5  # max restarts per watchdog session


@dataclass
class TlsSettings:
    """TLS configuration for the server."""

    mode: str = "off"
    certfile: str = ""
    keyfile: str = ""


@dataclass
class AuthSettings:
    """Authentication configuration for the server."""

    enabled: bool = True
    session_timeout: int = 2592000  # 30 days in seconds


@dataclass
class ServerSettings:
    """Server network settings (TLS, auth, CORS)."""

    tls: TlsSettings = field(default_factory=TlsSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)
    allowed_origins: list[str] = field(default_factory=list)


@dataclass
class DistroSettings:
    """Root settings object for the distro experience layer."""

    workspace_root: str = "~"
    identity: IdentitySettings = field(default_factory=IdentitySettings)
    backup: BackupSettings = field(default_factory=BackupSettings)
    slack: SlackSettings = field(default_factory=SlackSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    watchdog: WatchdogSettings = field(default_factory=WatchdogSettings)
    server: ServerSettings = field(default_factory=ServerSettings)


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------


def _settings_path() -> Path:
    """Return the distro settings file path."""
    return (
        Path(conventions.DISTRO_HOME).expanduser()
        / conventions.DISTRO_SETTINGS_FILENAME
    )


def _nested_from_dict(cls: type, data: dict[str, Any]) -> Any:
    """Recursively instantiate a dataclass from a dict, ignoring unknown keys.

    Uses ``typing.get_type_hints`` to resolve annotations that are stored as
    strings when ``from __future__ import annotations`` is active.
    """
    import dataclasses
    import typing

    try:
        hints = typing.get_type_hints(cls)
    except (NameError, AttributeError, TypeError):
        hints = {}

    filtered = {}
    for fld in dataclasses.fields(cls):
        if fld.name not in data:
            continue
        value = data[fld.name]
        fld_type = hints.get(fld.name)
        if (
            fld_type is not None
            and isinstance(fld_type, type)
            and dataclasses.is_dataclass(fld_type)
        ):
            filtered[fld.name] = (
                _nested_from_dict(fld_type, value) if isinstance(value, dict) else value
            )
        else:
            filtered[fld.name] = value
    return cls(**filtered)


def load() -> DistroSettings:
    """Load distro settings from disk, returning defaults for missing values."""
    path = _settings_path()
    if not path.exists():
        return DistroSettings()

    try:
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict):
            return DistroSettings()
        return _nested_from_dict(DistroSettings, raw)
    except (OSError, yaml.YAMLError):
        logger.warning("Failed to read distro settings from %s", path, exc_info=True)
        return DistroSettings()


def save(settings: DistroSettings) -> Path:
    """Persist distro settings to disk atomically. Returns the file path.

    Writes to a temporary file in the same directory then renames it into
    place.  On POSIX the rename is atomic, so a crash mid-write can never
    leave a truncated settings file.
    """
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.dump(asdict(settings), default_flow_style=False, sort_keys=False)

    # Write to a temp file in the same directory (same filesystem) so
    # os.replace() is guaranteed to be atomic on POSIX.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return path


def update(section: str | None = None, **kwargs: Any) -> DistroSettings:
    """Load, update one or more fields, save, and return the updated settings.

    If *section* is given (e.g. ``"slack"``), kwargs are applied to that
    nested dataclass.  Otherwise they are applied to the root.

    The entire load/modify/save cycle is protected by ``_settings_lock``
    so concurrent callers (settings page, install wizard, Slack setup)
    cannot clobber each other's writes.
    """
    with _settings_lock:
        settings = load()
        if section is not None:
            nested = getattr(settings, section)
            for key, value in kwargs.items():
                if hasattr(nested, key):
                    setattr(nested, key, value)
        else:
            for key, value in kwargs.items():
                if hasattr(settings, key):
                    setattr(settings, key, value)
        save(settings)
        return settings


# ---------------------------------------------------------------------------
# Environment export (called at server startup)
# ---------------------------------------------------------------------------

_VOICE_ENV_MAP: dict[str, str] = {
    "voice": "AMPLIFIER_VOICE_VOICE",
    "model": "AMPLIFIER_VOICE_MODEL",
    "instructions": "AMPLIFIER_VOICE_INSTRUCTIONS",
    "tools_enabled": "AMPLIFIER_VOICE_TOOLS_ENABLED",
    "assistant_name": "AMPLIFIER_VOICE_ASSISTANT_NAME",
}


def export_to_env(settings: DistroSettings | None = None) -> list[str]:
    """Export distro settings to environment variables (setdefault).

    Returns the list of env var names that were set.  Existing env vars
    always take precedence (setdefault semantics).
    """
    if settings is None:
        settings = load()

    exported: list[str] = []

    # Workspace root
    if settings.workspace_root:
        os.environ.setdefault("AMPLIFIER_WORKSPACE_ROOT", settings.workspace_root)
        exported.append("AMPLIFIER_WORKSPACE_ROOT")

    # Voice settings
    for field_name, env_key in _VOICE_ENV_MAP.items():
        value = getattr(settings.voice, field_name)
        if isinstance(value, bool):
            str_value = "true" if value else ""
        else:
            str_value = str(value)
        if str_value:
            os.environ.setdefault(env_key, str_value)
            exported.append(env_key)

    return exported
