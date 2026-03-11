"""Integration tests for the bundle prewarm flow.

Tests interactions between the distro plugin's routes, the loading screen
serving, and the reload wiring — without a real BundleRegistry.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from distro_plugin.config import DistroPluginSettings
from distro_plugin.routes import create_routes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockDistroState:
    """Minimal distro state object carrying settings for route handlers."""

    def __init__(self, settings: DistroPluginSettings) -> None:
        self.settings = settings


def _make_test_app(
    tmp_path,
    *,
    bundles_ready_set: bool = False,
    prewarm_error: str | None = None,
    with_overlay: bool = False,
) -> tuple[FastAPI, DistroPluginSettings]:
    """Create a minimal FastAPI test app with distro routes and mock state.

    Adds lightweight mock endpoints for ``/ready`` and ``/sessions`` that
    mirror the amplifierd versions but depend only on ``app.state`` values
    that the distro plugin controls.
    """
    settings = DistroPluginSettings(
        distro_home=tmp_path / "distro",
        amplifier_home=tmp_path / "amplifier",
    )

    app = FastAPI()
    app.state.distro = _MockDistroState(settings)

    # Prewarm state — mirrors what amplifierd sets up in _lifespan
    event = asyncio.Event()
    if bundles_ready_set:
        event.set()
    app.state.bundles_ready = event
    app.state.prewarm_error = prewarm_error

    # ── Mock /ready (normally served by amplifierd) ──────────────────────
    @app.get("/ready")
    async def ready_endpoint(request: Request):
        bundles_ready = getattr(request.app.state, "bundles_ready", None)
        ready = bool(bundles_ready and bundles_ready.is_set())
        error = getattr(request.app.state, "prewarm_error", None)
        return {"ready": ready, "error": error}

    # ── Mock /sessions guard (normally served by amplifierd) ─────────────
    @app.post("/sessions")
    async def sessions_endpoint(request: Request):
        bundles_ready = getattr(request.app.state, "bundles_ready", None)
        if bundles_ready is not None and not bundles_ready.is_set():
            return JSONResponse(
                status_code=503,
                content={"detail": "Bundles not ready — prewarm in progress"},
                headers={"Retry-After": "5"},
            )
        return JSONResponse({"session_id": "mock-session"})

    # Include the real distro plugin routes
    app.include_router(create_routes())

    # Optionally bootstrap an overlay so compute_phase != "unconfigured"
    if with_overlay:
        overlay_path = settings.distro_home / "bundle" / "bundle.yaml"
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_path.write_text("bundle: {name: test}\n")

    return app, settings


# ---------------------------------------------------------------------------
# Test 1: Full prewarm flow (loading → ready → dashboard)
# ---------------------------------------------------------------------------


def test_full_prewarm_flow(tmp_path):
    """End-to-end: loading screen during prewarm → ready → dashboard served."""
    app, settings = _make_test_app(tmp_path, bundles_ready_set=False, with_overlay=True)
    client = TestClient(app)

    # Phase 1: bundles_ready is unset — /distro/ must serve the loading screen
    resp = client.get("/distro/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "starting up" in resp.text.lower(), (
        "loading.html should contain 'starting up' text during prewarm"
    )

    # Phase 2: /ready reports {ready: false, error: null}
    resp = client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert data["error"] is None

    # Phase 3: Prewarm completes — simulate by setting the event
    app.state.bundles_ready.set()

    # Phase 4: /ready now reports {ready: true}
    resp = client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is True

    # Phase 5: /distro/ now serves the real dashboard (not the loading screen)
    resp = client.get("/distro/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "starting up" not in resp.text.lower(), (
        "Dashboard should not contain loading screen content after prewarm"
    )


# ---------------------------------------------------------------------------
# Test 2: Prewarm error flow (failure → loading screen shows error)
# ---------------------------------------------------------------------------


def test_prewarm_error_flow(tmp_path):
    """Error path: prewarm_error set → /ready reflects it; loading screen still served."""
    app, settings = _make_test_app(
        tmp_path,
        bundles_ready_set=False,
        prewarm_error="Network timeout",
        with_overlay=True,
    )
    client = TestClient(app)

    # /ready reports the error with ready=false
    resp = client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert data["error"] == "Network timeout"

    # /distro/ still serves the loading screen (not the dashboard or a redirect)
    resp = client.get("/distro/", follow_redirects=False)
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "starting up" in resp.text.lower(), (
        "Loading screen should still be served even after a prewarm error"
    )


# ---------------------------------------------------------------------------
# Test 3: Overlay change triggers reload
# ---------------------------------------------------------------------------


def test_overlay_change_triggers_reload(tmp_path, monkeypatch):
    """add_include on the overlay calls request_reload with the app object."""
    import distro_plugin.reload as reload_mod

    settings = DistroPluginSettings(
        distro_home=tmp_path / "distro",
        amplifier_home=tmp_path / "amplifier",
    )
    mock_app = SimpleNamespace()
    reload_calls: list[object] = []

    monkeypatch.setattr(
        reload_mod, "request_reload", lambda app: reload_calls.append(app)
    )

    from distro_plugin.overlay import add_include

    add_include(settings, "git+https://example.com/test@main", app=mock_app)

    assert len(reload_calls) == 1, (
        "request_reload should be called exactly once after add_include"
    )
    assert reload_calls[0] is mock_app, (
        "request_reload must receive the same app object passed to add_include"
    )


# ---------------------------------------------------------------------------
# Test 4: 503 guard on session creation during prewarm
# ---------------------------------------------------------------------------


def test_503_guard_during_prewarm(tmp_path):
    """POST /sessions returns 503 with Retry-After header while bundles_ready is unset."""
    app, _ = _make_test_app(tmp_path, bundles_ready_set=False)
    client = TestClient(app)

    resp = client.post("/sessions", json={"bundle_name": "distro"})

    assert resp.status_code == 503, (
        "Session creation must be blocked (503) while bundle prewarm is running"
    )
    assert "Retry-After" in resp.headers, (
        "503 response must include a Retry-After header so clients know to back off"
    )
