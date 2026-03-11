"""Tests for distro_plugin.reload — debounced bundle reload with serial cancellation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(*, has_registry: bool = True) -> SimpleNamespace:
    """Build a minimal app-like namespace for testing."""
    app = SimpleNamespace()
    app.state = SimpleNamespace()
    app.state.background_tasks = set()
    app.state.bundles_ready = asyncio.Event()
    app.state.prewarm_error = None
    app.state.prewarm_task = None

    if has_registry:
        registry = MagicMock()
        registry.update = AsyncMock()
        registry.register = MagicMock()
        app.state.bundle_registry = registry
    else:
        app.state.bundle_registry = None

    settings = SimpleNamespace(default_bundle="distro")
    app.state.settings = settings

    return app


# ---------------------------------------------------------------------------
# Test 1: _do_reload cancels in-flight prewarm and starts a new one
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_request_reload_cancels_and_restarts():
    """_do_reload cancels the old prewarm task and creates a fresh one."""
    app = _make_app()

    # Create a never-finishing task to simulate in-flight prewarm
    async def _forever():
        await asyncio.sleep(9999)

    old_task = asyncio.create_task(_forever())
    app.state.prewarm_task = old_task

    async def fake_prewarm(a):
        pass

    with (
        patch(
            "distro_plugin.reload.DistroPluginSettings",
            return_value=MagicMock(distro_home="/fake/home"),
        ),
        patch("distro_plugin.reload.overlay_exists", return_value=False),
        patch("distro_plugin.reload.prewarm", fake_prewarm),
    ):
        from distro_plugin.reload import _do_reload

        await _do_reload(app)

    # Old task must have been cancelled
    assert old_task.cancelled(), "old prewarm task should have been cancelled"

    # A new task must have been started
    assert app.state.prewarm_task is not None, "new prewarm_task should be set"
    assert app.state.prewarm_task is not old_task, "new task must differ from old task"

    # Clean up the new task to avoid event-loop noise
    if app.state.prewarm_task and not app.state.prewarm_task.done():
        app.state.prewarm_task.cancel()
        try:
            await app.state.prewarm_task
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# Test 2: debounce coalesces rapid calls into one _do_reload execution
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reload_debounces_rapid_calls():
    """Five rapid request_reload() calls with 0.1 s debounce fire _do_reload only once."""
    call_count = 0

    async def counting_do_reload(a):
        nonlocal call_count
        call_count += 1

    app = _make_app()

    with patch("distro_plugin.reload._do_reload", counting_do_reload):
        import distro_plugin.reload as reload_mod

        # Reset module-level handle before test
        reload_mod._debounce_handle = None

        for _ in range(5):
            reload_mod.request_reload(app, debounce_seconds=0.1)

        # Wait long enough for the debounce to fire (2× the debounce window)
        await asyncio.sleep(0.3)

    assert call_count == 1, f"expected 1 reload execution, got {call_count}"

    # Clean up lingering handle
    import distro_plugin.reload as reload_mod

    if reload_mod._debounce_handle is not None:
        reload_mod._debounce_handle.cancel()
        reload_mod._debounce_handle = None


# ---------------------------------------------------------------------------
# Test 3: _do_reload is a no-op when there is no bundle_registry
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reload_handles_no_registry():
    """_do_reload logs a warning and returns cleanly when bundle_registry is absent."""
    app = _make_app(has_registry=False)

    # Should not raise
    from distro_plugin.reload import _do_reload

    await _do_reload(app)  # no exception expected

    # prewarm_task must still be None — nothing was started
    assert app.state.prewarm_task is None


# ---------------------------------------------------------------------------
# Test 4: _do_reload calls registry.register() with overlay path when overlay exists
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reload_reregisters_overlay(tmp_path):
    """registry.register() is called with the overlay dir when overlay_exists() is True."""
    distro_home = tmp_path / "distro"

    app = _make_app()

    fake_settings = MagicMock()
    fake_settings.distro_home = str(distro_home)

    async def fake_prewarm(a):
        pass

    with (
        patch(
            "distro_plugin.reload.DistroPluginSettings",
            return_value=fake_settings,
        ),
        patch("distro_plugin.reload.overlay_exists", return_value=True),
        patch("distro_plugin.reload.prewarm", fake_prewarm),
    ):
        from distro_plugin.reload import _do_reload

        await _do_reload(app)

    expected_overlay_dir = str(distro_home / "bundle")
    app.state.bundle_registry.register.assert_called_once_with(
        {"distro": expected_overlay_dir}
    )

    # Clean up new task
    if app.state.prewarm_task and not app.state.prewarm_task.done():
        app.state.prewarm_task.cancel()
        try:
            await app.state.prewarm_task
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# Test 5: _do_reload calls registry.update() to invalidate cache
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reload_calls_registry_update():
    """_do_reload calls registry.update(default_bundle) to bypass _loaded_bundles cache."""
    app = _make_app()

    async def fake_prewarm(a):
        pass

    with (
        patch(
            "distro_plugin.reload.DistroPluginSettings",
            return_value=MagicMock(distro_home="/fake"),
        ),
        patch("distro_plugin.reload.overlay_exists", return_value=False),
        patch("distro_plugin.reload.prewarm", fake_prewarm),
    ):
        from distro_plugin.reload import _do_reload

        await _do_reload(app)

    app.state.bundle_registry.update.assert_awaited_once_with("distro")

    # Clean up new task
    if app.state.prewarm_task and not app.state.prewarm_task.done():
        app.state.prewarm_task.cancel()
        try:
            await app.state.prewarm_task
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# Test 6: _do_reload clears bundles_ready event before starting new prewarm
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reload_clears_prepared_bundle():
    """_do_reload calls session_manager.clear_prepared_bundle() so stale bundles are removed.

    After a reload, the stale PreparedBundle must be cleared so subsequent
    session creation doesn't accidentally reuse an out-of-date bundle.
    """
    app = _make_app()
    # Simulate a session_manager with a stale prepared bundle
    mock_session_manager = MagicMock()
    app.state.session_manager = mock_session_manager

    async def fake_prewarm(a):
        pass

    with (
        patch(
            "distro_plugin.reload.DistroPluginSettings",
            return_value=MagicMock(distro_home="/fake"),
        ),
        patch("distro_plugin.reload.overlay_exists", return_value=False),
        patch("distro_plugin.reload.prewarm", fake_prewarm),
    ):
        from distro_plugin.reload import _do_reload

        await _do_reload(app)

    # session_manager.clear_prepared_bundle() must have been called
    mock_session_manager.clear_prepared_bundle.assert_called_once_with()

    # Clean up new task
    if app.state.prewarm_task and not app.state.prewarm_task.done():
        app.state.prewarm_task.cancel()
        try:
            await app.state.prewarm_task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.anyio
async def test_reload_clears_bundles_ready():
    """_do_reload clears bundles_ready so callers must wait on the new prewarm."""
    app = _make_app()
    # Pre-set the event as if bundles were previously ready
    app.state.bundles_ready.set()

    async def fake_prewarm(a):
        # Does NOT set bundles_ready — so it stays clear after _do_reload
        pass

    with (
        patch(
            "distro_plugin.reload.DistroPluginSettings",
            return_value=MagicMock(distro_home="/fake"),
        ),
        patch("distro_plugin.reload.overlay_exists", return_value=False),
        patch("distro_plugin.reload.prewarm", fake_prewarm),
    ):
        from distro_plugin.reload import _do_reload

        await _do_reload(app)

    # bundles_ready should have been cleared at reload time
    assert not app.state.bundles_ready.is_set(), (
        "bundles_ready should be cleared before new prewarm starts"
    )
    assert app.state.prewarm_error is None

    # Clean up new task
    if app.state.prewarm_task and not app.state.prewarm_task.done():
        app.state.prewarm_task.cancel()
        try:
            await app.state.prewarm_task
        except (asyncio.CancelledError, Exception):
            pass
