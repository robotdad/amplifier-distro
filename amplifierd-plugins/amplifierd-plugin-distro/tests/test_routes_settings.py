"""Tests for GET/POST /distro/distro-settings and static HTML page endpoints."""

from __future__ import annotations


from distro_plugin.distro_settings import load as load_distro_settings


def test_get_distro_settings_returns_dict_and_path(settings, client):
    """GET /distro/distro-settings returns settings dict with path; default workspace_root is '~'."""
    resp = client.get("/distro/distro-settings")
    assert resp.status_code == 200

    data = resp.json()
    assert "settings" in data
    assert "path" in data
    assert data["settings"]["workspace_root"] == "~"


def test_post_distro_settings_updates_root_field(settings, client):
    """POST /distro/distro-settings updates a root-level field (workspace_root)."""
    resp = client.post(
        "/distro/distro-settings",
        json={"values": {"workspace_root": "/tmp/test-ws"}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    # Verify persisted
    ds = load_distro_settings(settings)
    assert ds.workspace_root == "/tmp/test-ws"


def test_post_distro_settings_updates_section_field(settings, client):
    """POST /distro/distro-settings updates a section field (identity.github_handle)."""
    resp = client.post(
        "/distro/distro-settings",
        json={"section": "identity", "values": {"github_handle": "octocat"}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    # Verify persisted
    ds = load_distro_settings(settings)
    assert ds.identity.github_handle == "octocat"


def test_get_setup_returns_500_when_static_missing(settings, tmp_path, monkeypatch):
    """GET /distro/setup returns 500 HTML fallback when wizard.html is missing."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Point _STATIC_DIR to an empty tmp directory so wizard.html is missing
    monkeypatch.setattr("distro_plugin.routes._STATIC_DIR", tmp_path)

    from distro_plugin.routes import create_routes

    app = FastAPI()
    app.state.distro = type("S", (), {"settings": settings})()
    app.include_router(create_routes())

    client = TestClient(app)
    resp = client.get("/distro/setup")
    assert resp.status_code == 500
    assert "text/html" in resp.headers.get("content-type", "")
    assert "not available" in resp.text


def test_get_settings_page_returns_500_when_static_missing(
    settings, tmp_path, monkeypatch
):
    """GET /distro/settings returns 500 HTML fallback when settings.html is missing."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Point _STATIC_DIR to an empty tmp directory so settings.html is missing
    monkeypatch.setattr("distro_plugin.routes._STATIC_DIR", tmp_path)

    from distro_plugin.routes import create_routes

    app = FastAPI()
    app.state.distro = type("S", (), {"settings": settings})()
    app.include_router(create_routes())

    client = TestClient(app)
    resp = client.get("/distro/settings")
    assert resp.status_code == 500
    assert "text/html" in resp.headers.get("content-type", "")
    assert "not available" in resp.text
