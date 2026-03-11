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
# Overlay migration tables
# ---------------------------------------------------------------------------

# URIs that should be silently removed (no replacement).
_STALE_URIS: list[str] = [
    # hooks-session-naming is now properly declared in the distro bundle at
    # https://github.com/microsoft/amplifier-bundle-distro/blob/main/behaviors/start.yaml
    # No longer needs migration stripping.
]

# URIs that moved — old URI → current URI.  The overlay migration replaces
# these in-place so the include order is preserved.
_URI_REPLACEMENTS: dict[str, str] = {
    # Distro bundle moved from monorepo subdirectory to its own repo.
    "git+https://github.com/microsoft/amplifier-distro@main#subdirectory=bundle": (
        _DISTRO_BUNDLE_URI
    ),
    # Provider bundles that never existed in foundation; now live in the
    # distro bundle repo.
    "git+https://github.com/microsoft/amplifier-foundation@main"
    "#subdirectory=providers/gemini-pro.yaml": (
        f"{_DISTRO_BUNDLE_URI}#subdirectory=providers/gemini-pro.yaml"
    ),
    "git+https://github.com/microsoft/amplifier-foundation@main"
    "#subdirectory=providers/ollama.yaml": (
        f"{_DISTRO_BUNDLE_URI}#subdirectory=providers/ollama.yaml"
    ),
    "git+https://github.com/microsoft/amplifier-foundation@main"
    "#subdirectory=providers/azure-openai.yaml": (
        f"{_DISTRO_BUNDLE_URI}#subdirectory=providers/azure-openai.yaml"
    ),
}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _overlay_path(settings: DistroPluginSettings) -> Path:
    """Return the path to the overlay bundle.yaml."""
    return Path(settings.distro_home) / "bundle" / "bundle.yaml"


def _write_overlay(
    settings: DistroPluginSettings, data: dict[str, Any], *, app: Any = None
) -> Path:
    """Write the overlay bundle.yaml to disk."""
    path = _overlay_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))

    # Trigger debounced bundle reload if app is available
    if app is not None:
        from distro_plugin.reload import request_reload

        request_reload(app)

    return path


def _filter_includes(includes: list[Any], uri: str) -> list[Any]:
    """Return *includes* with every entry matching *uri* removed."""
    return [
        entry
        for entry in includes
        if (entry.get("bundle") if isinstance(entry, dict) else entry) != uri
    ]


def _migrate_includes(data: dict[str, Any]) -> bool:
    """Apply all known URI migrations to overlay includes.

    1. Replace moved URIs with their current equivalents (in-place).
    2. Remove stale URIs that have no replacement.

    Returns ``True`` if any entries were changed.
    """
    includes: list[Any] = data.get("includes", [])
    changed = False

    # Pass 1: in-place replacements for moved URIs.
    for i, entry in enumerate(includes):
        uri = entry.get("bundle") if isinstance(entry, dict) else entry
        if uri in _URI_REPLACEMENTS:
            new_uri = _URI_REPLACEMENTS[uri]
            includes[i] = {"bundle": new_uri} if isinstance(entry, dict) else new_uri
            changed = True

    # Pass 2: remove entries with no replacement.
    original_len = len(includes)
    for uri in _STALE_URIS:
        includes = _filter_includes(includes, uri)
    if len(includes) < original_len:
        changed = True

    data["includes"] = includes
    return changed


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


def migrate_overlay(settings: DistroPluginSettings) -> None:
    """Apply one-time migrations to the overlay bundle.

    Replaces moved include URIs with their current equivalents,
    removes stale URIs, and ensures the distro bundle include
    is present at position 0.  No-op when no overlay exists or
    no changes are needed.
    """
    data = read_overlay(settings)
    if not data:
        return

    changed = _migrate_includes(data)

    # Ensure the current distro bundle URI is present at position 0.
    current_uris = [
        entry["bundle"] if isinstance(entry, dict) else entry
        for entry in data.get("includes", [])
    ]
    if _DISTRO_BUNDLE_URI not in current_uris:
        data.setdefault("includes", []).insert(0, {"bundle": _DISTRO_BUNDLE_URI})
        changed = True

    if changed:
        _write_overlay(settings, data)
        logger.info("Overlay migrated: stale URIs replaced with current equivalents")


def add_include(settings: DistroPluginSettings, uri: str, *, app: Any = None) -> None:
    """Add a bundle include to the overlay (idempotent).

    If the overlay does not exist it is bootstrapped with the distro
    bundle URI as the first include.  On existing overlays, stale URIs
    are migrated and ``_DISTRO_BUNDLE_URI`` is ensured to be present.

    When *app* is provided, a debounced bundle reload is triggered after
    writing via :func:`distro_plugin.reload.request_reload`.
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
    else:
        # Migrate stale entries first so current_uris reflects the current state.
        _migrate_includes(data)
        # Ensure the distro bundle URI is present (may be absent in old overlays).
        existing = {
            entry["bundle"] if isinstance(entry, dict) else entry
            for entry in data.get("includes", [])
        }
        if _DISTRO_BUNDLE_URI not in existing:
            data.setdefault("includes", []).insert(0, {"bundle": _DISTRO_BUNDLE_URI})

    current_uris = {
        entry["bundle"] if isinstance(entry, dict) else entry
        for entry in data.get("includes", [])
    }

    if uri not in current_uris:
        data.setdefault("includes", []).append({"bundle": uri})

    _write_overlay(settings, data, app=app)


def remove_include(
    settings: DistroPluginSettings, uri: str, *, app: Any = None
) -> None:
    """Remove a bundle include from the overlay.

    When *app* is provided, a debounced bundle reload is triggered after
    writing via :func:`distro_plugin.reload.request_reload`.
    """
    data = read_overlay(settings)
    if not data:
        return

    data["includes"] = _filter_includes(data.get("includes", []), uri)
    _write_overlay(settings, data, app=app)
