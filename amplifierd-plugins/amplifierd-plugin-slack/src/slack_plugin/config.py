"""Configuration for the Slack bridge plugin.

All config comes from environment variables with SLACK_ prefix,
plus an optional keys.env file for secrets.

Priority order (highest wins):
1. Environment variables (SLACK_BOT_TOKEN, etc.)
2. keys.env for secrets
3. Defaults
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default paths
_DEFAULT_AMPLIFIER_HOME = "~/.amplifier"
_KEYS_FILENAME = "keys.env"


def _load_keys(amplifier_home: str = _DEFAULT_AMPLIFIER_HOME) -> dict[str, Any]:
    """Load keys.env if it exists (.env format)."""
    path = Path(amplifier_home).expanduser() / _KEYS_FILENAME
    if not path.exists():
        return {}
    result: dict[str, Any] = {}
    try:
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key:
                result[key] = value
    except OSError:
        logger.warning("Failed to read keys.env", exc_info=True)
    return result


def _env_str(env_key: str, fallback: str) -> str:
    """Return env var if set, else fallback."""
    val = os.environ.get(env_key, "")
    return val if val else fallback


def _env_bool(env_key: str, fallback: bool) -> bool:
    """Return env var as bool if set, else fallback."""
    val = os.environ.get(env_key, "")
    if val:
        return val.lower() in ("1", "true", "yes")
    return fallback


def _key_str(env_key: str, keys: dict[str, Any]) -> str:
    """Return env var > keys.env value, or empty string."""
    val = os.environ.get(env_key, "")
    if val:
        return val
    k = keys.get(env_key, "")
    return str(k) if k else ""


@dataclass
class SlackConfig:
    """Slack bridge configuration."""

    # --- Slack API Credentials (from keys.env) ---
    bot_token: str = ""  # xoxb-... (Bot User OAuth Token)
    app_token: str = ""  # xapp-... (for Socket Mode)
    signing_secret: str = ""  # For Events API verification

    # --- Channel Configuration ---
    hub_channel_id: str = ""
    hub_channel_name: str = "amplifier"

    # --- Behavior ---
    thread_per_session: bool = True
    allow_breakout: bool = True
    channel_prefix: str = "amp-"
    bot_name: str = "slackbridge"

    # --- Session Defaults ---
    default_bundle: str | None = None
    default_working_dir: str = "~"

    # --- Limits ---
    max_message_length: int = 3900
    response_timeout: int = 300

    # --- Mode ---
    simulator_mode: bool = False
    socket_mode: bool = False

    @classmethod
    def from_env(cls, amplifier_home: str = _DEFAULT_AMPLIFIER_HOME) -> SlackConfig:
        """Load config from keys.env + env vars.

        Priority: env vars > keys.env > defaults.
        """
        keys = _load_keys(amplifier_home)

        config = cls(
            bot_token=_key_str("SLACK_BOT_TOKEN", keys),
            app_token=_key_str("SLACK_APP_TOKEN", keys),
            signing_secret=_key_str("SLACK_SIGNING_SECRET", keys),
            hub_channel_id=_env_str("SLACK_HUB_CHANNEL_ID", ""),
            hub_channel_name=_env_str("SLACK_HUB_CHANNEL_NAME", "amplifier"),
            default_working_dir=_env_str("SLACK_DEFAULT_WORKING_DIR", "~"),
            simulator_mode=_env_bool("SLACK_SIMULATOR_MODE", False),
            socket_mode=_env_bool("SLACK_SOCKET_MODE", False),
            thread_per_session=_env_bool("SLACK_THREAD_PER_SESSION", True),
            allow_breakout=_env_bool("SLACK_ALLOW_BREAKOUT", True),
            channel_prefix=_env_str("SLACK_CHANNEL_PREFIX", "amp-"),
            bot_name=_env_str("SLACK_BOT_NAME", "slackbridge"),
            default_bundle=_env_str("SLACK_DEFAULT_BUNDLE", "") or None,
            max_message_length=int(_env_str("SLACK_MAX_MESSAGE_LENGTH", "3900")),
            response_timeout=int(_env_str("SLACK_RESPONSE_TIMEOUT", "300")),
        )
        logger.debug(
            "SlackConfig.from_env: default_working_dir=%s",
            config.default_working_dir,
        )
        return config

    @property
    def is_configured(self) -> bool:
        """Whether the Slack credentials are configured."""
        if self.socket_mode:
            return bool(self.bot_token and self.app_token)
        return bool(self.bot_token and self.signing_secret)

    @property
    def mode(self) -> str:
        """Current operating mode."""
        if self.simulator_mode:
            return "simulator"
        if self.socket_mode and self.bot_token and self.app_token:
            return "socket"
        if self.is_configured:
            return "events-api"
        return "unconfigured"
