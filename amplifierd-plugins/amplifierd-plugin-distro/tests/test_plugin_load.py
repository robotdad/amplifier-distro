"""Tests for plugin entry-point loading."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter
from starlette.routing import Route


def test_create_router_returns_api_router():
    """create_router returns a FastAPI APIRouter instance."""
    from distro_plugin import create_router

    state = SimpleNamespace()
    router = create_router(state)
    assert isinstance(router, APIRouter)


def test_create_router_registers_all_endpoints():
    """create_router registers all expected endpoints on the router."""
    from distro_plugin import create_router

    state = SimpleNamespace()
    router = create_router(state)

    # Collect (path, method) pairs, excluding implicit HEAD methods.
    registered: set[tuple[str, str]] = set()
    for route in router.routes:
        if not isinstance(route, Route):
            continue
        for method in route.methods or ():
            if method != "HEAD":
                registered.add((route.path, method))

    # 16 unique paths, 17 route handlers (distro-settings has GET + POST).
    expected = {
        ("/distro/status", "GET"),
        ("/distro/detect", "GET"),
        ("/distro/providers", "GET"),
        ("/distro/provider", "POST"),
        ("/distro/modules", "GET"),
        ("/distro/features", "POST"),
        ("/distro/tier", "POST"),
        ("/distro/setup/steps/welcome", "POST"),
        ("/distro/setup/steps/config", "POST"),
        ("/distro/setup/steps/modules", "POST"),
        ("/distro/setup/steps/interfaces", "POST"),
        ("/distro/setup/steps/provider", "POST"),
        ("/distro/setup/steps/verify", "POST"),
        ("/distro/distro-settings", "GET"),
        ("/distro/distro-settings", "POST"),
        ("/distro/setup", "GET"),
        ("/distro/settings", "GET"),
    }

    for path, method in expected:
        assert (path, method) in registered, f"Missing route: {method} {path}"

    # Verify /distro/distro-settings has exactly 2 route handlers (GET + POST).
    ds_methods = {m for p, m in registered if p == "/distro/distro-settings"}
    assert ds_methods == {"GET", "POST"}, (
        f"Expected GET+POST for /distro/distro-settings, got {ds_methods}"
    )


def test_main_create_app_returns_configured_fastapi():
    """create_app factory returns a FastAPI app with plugin routes mounted."""
    from fastapi import FastAPI

    from distro_plugin.__main__ import create_app

    app = create_app()
    assert isinstance(app, FastAPI)

    # Verify plugin routes are included.
    paths = {route.path for route in app.routes if isinstance(route, Route)}
    assert "/distro/status" in paths, "Expected /distro/status in app routes"
