"""amplifierd-plugin-distro — distro setup wizard and settings management."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter

from distro_plugin.config import DistroPluginSettings
from distro_plugin.routes import create_routes


def create_router(state: Any) -> APIRouter:
    """Plugin entry point called by amplifierd to discover and mount routes.

    Instantiates ``DistroPluginSettings`` from environment, attaches it to
    *state* so route handlers can retrieve it via
    ``request.app.state.distro.settings``, and returns the ``APIRouter``.

    Also runs overlay migration at startup so any stale URIs from previous
    installations are silently upgraded to current equivalents.
    """
    settings = DistroPluginSettings()
    state.distro = SimpleNamespace(settings=settings)

    from distro_plugin.overlay import migrate_overlay, overlay_exists

    migrate_overlay(settings)

    # Register the overlay bundle as "distro", shadowing the well-known git URI.
    # This ensures prewarm and session creation use the user's customized bundle
    # (with their selected providers and features) instead of the raw upstream.
    # The overlay's bundle.yaml includes the upstream distro bundle via includes:.
    bundle_registry = getattr(state, "bundle_registry", None)
    if bundle_registry and overlay_exists(settings):
        from pathlib import Path

        overlay_dir = str(Path(settings.distro_home) / "bundle")
        bundle_registry.register({"distro": overlay_dir})

    return create_routes()
