"""Debounced bundle reload for the distro plugin.

When the overlay bundle changes (user adds/removes providers or features),
this module cancels any in-flight prewarm, invalidates the bundle cache
via registry.update(), and starts a fresh prewarm task.

Rapid overlay mutations (e.g., toggling multiple features in the wizard)
are debounced — only one reload fires after a burst of changes.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from distro_plugin.config import DistroPluginSettings
from distro_plugin.overlay import overlay_exists

# Lazy import of prewarm from amplifierd — amplifierd is only available at
# runtime (not in tests unless installed).  We expose a module-level name so
# tests can patch distro_plugin.reload.prewarm without importing amplifierd.
try:
    from amplifierd.app import prewarm  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    prewarm = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Module-level state for debounce timer
_debounce_handle: asyncio.TimerHandle | None = None


def request_reload(app: Any, *, debounce_seconds: float = 0.5) -> None:
    """Request a debounced bundle reload.

    Multiple rapid calls coalesce into a single reload after the debounce
    period. Safe to call from synchronous route handlers.
    """
    global _debounce_handle

    loop = asyncio.get_running_loop()

    # Cancel any pending debounce timer
    if _debounce_handle is not None:
        _debounce_handle.cancel()
        _debounce_handle = None

    # Schedule the actual reload after debounce period
    _debounce_handle = loop.call_later(
        debounce_seconds,
        lambda: asyncio.ensure_future(_do_reload(app)),
    )


async def _do_reload(app: Any) -> None:
    """Execute the actual reload: cancel old task, invalidate cache, restart prewarm."""
    global _debounce_handle
    _debounce_handle = None

    registry = getattr(app.state, "bundle_registry", None)
    if not registry:
        logger.warning("Cannot reload: no bundle_registry on app.state")
        return

    settings = getattr(app.state, "settings", None)
    default_bundle = getattr(settings, "default_bundle", None) if settings else None
    if not default_bundle:
        logger.warning("Cannot reload: no default_bundle configured")
        return

    # 1. Cancel in-flight prewarm task — SERIAL cancellation
    #
    # NOTE: asyncio.to_thread() does not interrupt the worker thread —
    # cancel() only detaches the awaiter. The thread running subprocess.run()
    # (uv pip install) will complete on its own. This means a brief period of
    # overlapping work is possible, but uv uses file locks so this is safe.
    # We await the old task to ensure we don't start a NEW prewarm until
    # the cancellation is processed in the async layer.
    old_task = getattr(app.state, "prewarm_task", None)
    if old_task and not old_task.done():
        old_task.cancel()
        try:
            await old_task
        except (asyncio.CancelledError, Exception):
            pass
        logger.info("Cancelled in-flight prewarm task")

    # 2. Re-register overlay (in case it was just created by the wizard)
    # Prefer stored settings from app.state to avoid re-reading env/disk on
    # every reload; fall back to constructing fresh settings if unavailable.
    distro_state = getattr(app.state, "distro", None)
    plugin_settings = distro_state.settings if distro_state else None
    if not plugin_settings:
        plugin_settings = DistroPluginSettings()
    if overlay_exists(plugin_settings):
        overlay_dir = str(Path(plugin_settings.distro_home) / "bundle")
        registry.register({"distro": overlay_dir})

    # 3. Invalidate cache — registry.update() bypasses _loaded_bundles cache
    try:
        await registry.update(default_bundle)
    except Exception:
        logger.warning("registry.update('%s') failed", default_bundle, exc_info=True)

    # 4. Clear readiness state
    bundles_ready = getattr(app.state, "bundles_ready", None)
    if bundles_ready:
        bundles_ready.clear()
    app.state.prewarm_error = None
    session_manager = getattr(app.state, "session_manager", None)
    if session_manager and hasattr(session_manager, "clear_prepared_bundle"):
        session_manager.clear_prepared_bundle()

    # 5. Start new prewarm task — uses module-level prewarm (patchable in tests)
    if prewarm is None:  # pragma: no cover
        logger.warning("Cannot start new prewarm: amplifierd.prewarm not available")
        return
    new_task = asyncio.create_task(prewarm(app))
    app.state.prewarm_task = new_task
    background_tasks = getattr(app.state, "background_tasks", None)
    if background_tasks is not None:
        background_tasks.add(new_task)
        new_task.add_done_callback(background_tasks.discard)

    logger.info("Bundle reload triggered — new prewarm started")
