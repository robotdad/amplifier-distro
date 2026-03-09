"""Local overlay bundle management.

The distro creates a local bundle that includes the maintained distro bundle.
Settings apps modify this overlay; the underlying distro bundle is never
touched.

The overlay is a directory containing a ``bundle.yaml`` file:

    ~/.amplifier-distro/bundle/
    └── bundle.yaml

Foundation's ``load_bundle()`` loads it by path and handles all
include resolution and composition automatically.

All public functions take ``settings: DistroPluginSettings`` as the first
parameter to determine the distro home directory.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from distro_plugin.config import DistroPluginSettings

logger = logging.getLogger(__name__)

_DISTRO_BUNDLE_URI = "git+https://github.com/microsoft/amplifier-bundle-distro@main"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _overlay_path(settings: DistroPluginSettings) -> Path:
    """Return the path to the overlay bundle.yaml."""
    return Path(settings.distro_home) / "bundle" / "bundle.yaml"


def _write_overlay(settings: DistroPluginSettings, data: dict[str, Any]) -> Path:
    """Write the overlay bundle.yaml to disk."""
    path = _overlay_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return path


def _filter_includes(includes: list[Any], uri: str) -> list[Any]:
    """Return *includes* with every entry matching *uri* removed."""
    return [
        entry
        for entry in includes
        if (entry.get("bundle") if isinstance(entry, dict) else entry) != uri
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def overlay_exists(settings: DistroPluginSettings) -> bool:
    """Check whether the local overlay bundle has been created."""
    return _overlay_path(settings).exists()


def read_overlay(settings: DistroPluginSettings) -> dict[str, Any]:
    """Read and parse the current overlay bundle. Returns {} if missing."""
    path = _overlay_path(settings)
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except (yaml.YAMLError, OSError):
        logger.warning(
            "Overlay bundle at %s is corrupt or unreadable; treating as absent",
            path,
        )
        return {}


def get_includes(settings: DistroPluginSettings) -> list[str]:
    """Extract the list of include URIs from the overlay."""
    data = read_overlay(settings)
    return [
        entry["bundle"] if isinstance(entry, dict) else entry
        for entry in data.get("includes", [])
    ]


def add_include(settings: DistroPluginSettings, uri: str) -> None:
    """Add a bundle include to the overlay (idempotent).

    If the overlay does not exist it is bootstrapped with the distro
    bundle URI as the first include.
    """
    data = read_overlay(settings)

    if not data:
        # Bootstrap a fresh overlay with the distro bundle.
        data = {
            "bundle": {
                "name": "amplifier-distro",
                "version": "0.1.0",
                "description": "Local Amplifier Distro environment",
            },
            "includes": [{"bundle": _DISTRO_BUNDLE_URI}],
        }

    current_uris = {
        entry["bundle"] if isinstance(entry, dict) else entry
        for entry in data.get("includes", [])
    }

    if uri not in current_uris:
        data.setdefault("includes", []).append({"bundle": uri})

    _write_overlay(settings, data)


def remove_include(settings: DistroPluginSettings, uri: str) -> None:
    """Remove a bundle include from the overlay."""
    data = read_overlay(settings)
    if not data:
        return

    data["includes"] = _filter_includes(data.get("includes", []), uri)
    _write_overlay(settings, data)
