"""Tests for POST /distro/setup/steps/* wizard endpoints."""

from __future__ import annotations

from distro_plugin.distro_settings import load as load_distro_settings
from distro_plugin.features import FEATURES, get_enabled_features
from distro_plugin.overlay import add_include


def test_step_welcome_saves_identity_data(settings, client):
    """POST /distro/setup/steps/welcome saves workspace_root, github_handle, git_email."""
    resp = client.post(
        "/distro/setup/steps/welcome",
        json={
            "workspace_root": "/home/user/projects",
            "github_handle": "testuser",
            "git_email": "test@example.com",
        },
    )
    assert resp.status_code == 200

    data = resp.json()
    assert data.get("status") == "ok"

    # Verify data was persisted via distro_settings
    ds = load_distro_settings(settings)
    assert ds.workspace_root == "/home/user/projects"
    assert ds.identity.github_handle == "testuser"
    assert ds.identity.git_email == "test@example.com"


def test_step_config_returns_ok(client):
    """POST /distro/setup/steps/config returns ok (passthrough)."""
    resp = client.post("/distro/setup/steps/config", json={})
    assert resp.status_code == 200

    data = resp.json()
    assert data.get("status") == "ok"


def test_step_modules_enables_requested_disables_unrequested(settings, client):
    """POST /distro/setup/steps/modules enables requested and disables unrequested."""
    # Pre-enable two features so we can verify disable behavior
    for inc in FEATURES["dev-memory"].includes:
        add_include(settings, inc)
    for inc in FEATURES["deliberate-dev"].includes:
        add_include(settings, inc)

    # Request only dev-memory; deliberate-dev should get disabled
    resp = client.post(
        "/distro/setup/steps/modules",
        json={"modules": ["dev-memory"]},
    )
    assert resp.status_code == 200

    data = resp.json()
    assert data.get("status") == "ok"

    enabled = get_enabled_features(settings)
    assert "dev-memory" in enabled
    assert "deliberate-dev" not in enabled


def test_step_interfaces_skip_returns_ok(client):
    """POST /distro/setup/steps/interfaces with empty list returns ok with install flags."""
    resp = client.post(
        "/distro/setup/steps/interfaces",
        json={"interfaces": []},
    )
    assert resp.status_code == 200

    data = resp.json()
    assert data.get("status") == "ok"
    assert data["cli_installed"] is False
    assert data["tui_installed"] is False


def test_step_provider_with_key_registers(settings, client, monkeypatch):
    """POST /distro/setup/steps/provider with a key registers the provider."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    resp = client.post(
        "/distro/setup/steps/provider",
        json={"provider": "anthropic", "api_key": "sk-ant-test-key-12345"},
    )
    assert resp.status_code == 200

    data = resp.json()
    assert data.get("status") == "ok"
    assert data.get("provider") == "anthropic"


def test_step_provider_sync_mode(settings, client, monkeypatch):
    """POST /distro/setup/steps/provider with empty provider+key triggers sync."""
    called_with = []
    monkeypatch.setattr(
        "distro_plugin.routes.sync_providers",
        lambda s: (called_with.append(s), [])[1],
    )

    resp = client.post(
        "/distro/setup/steps/provider",
        json={"provider": "", "api_key": ""},
    )
    assert resp.status_code == 200

    data = resp.json()
    assert data.get("status") == "ok"
    assert "synced" in data
    assert len(called_with) == 1


def test_step_verify_returns_status(settings, client):
    """POST /distro/setup/steps/verify returns phase, ready, overlay_exists."""
    resp = client.post("/distro/setup/steps/verify", json={})
    assert resp.status_code == 200

    data = resp.json()
    assert "phase" in data
    assert "ready" in data
    assert "overlay_exists" in data
    # Default: unconfigured, not ready, no overlay
    assert data["phase"] == "unconfigured"
    assert data["ready"] is False
    assert data["overlay_exists"] is False
