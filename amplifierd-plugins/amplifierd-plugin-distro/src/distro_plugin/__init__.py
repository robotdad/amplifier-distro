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

    from distro_plugin.overlay import migrate_overlay

    migrate_overlay(settings)

    return create_routes()
