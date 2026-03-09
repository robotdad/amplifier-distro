"""Tests for GET /distro/status and GET /distro/detect endpoints."""

from __future__ import annotations


def test_status_returns_expected_structure_defaults_unconfigured(client):
    """GET /distro/status returns phase, provider, features, bridges; defaults to 'unconfigured'."""
    resp = client.get("/distro/status")
    assert resp.status_code == 200

    data = resp.json()

    # Required top-level keys
    assert "phase" in data
    assert "provider" in data
    assert "features" in data
    assert "bridges" in data

    # Default phase when nothing is configured
    assert data["phase"] == "unconfigured"


def test_detect_returns_expected_structure(client):
    """GET /distro/detect returns github, git, api_keys, workspace_candidates, bridges, and flat convenience fields."""
    resp = client.get("/distro/detect")
    assert resp.status_code == 200

    data = resp.json()

    # Required top-level keys
    assert "github" in data
    assert "git" in data
    assert "api_keys" in data
    assert "workspace_candidates" in data
    assert "bridges" in data

    # Flat convenience fields should exist
    assert "github_user" in data
    assert "git_name" in data
    assert "git_email" in data


def test_status_phase_ready_when_overlay_and_provider(settings, client, monkeypatch):
    """Status phase becomes 'ready' when overlay exists and a provider key is set."""
    from distro_plugin.overlay import add_include
    from distro_plugin.providers import PROVIDERS

    # Create the overlay so overlay_exists() returns True
    add_include(settings, PROVIDERS["anthropic"].include)

    # Set a provider API key in environment
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-12345")

    resp = client.get("/distro/status")
    assert resp.status_code == 200

    data = resp.json()
    assert data["phase"] == "ready"


def test_status_phase_detected_when_overlay_but_no_provider_key(
    settings, client, monkeypatch
):
    """Status phase becomes 'detected' when overlay exists but no provider key is set."""
    from distro_plugin.overlay import add_include
    from distro_plugin.providers import PROVIDERS

    # Create the overlay so overlay_exists() returns True
    add_include(settings, PROVIDERS["anthropic"].include)

    # Ensure no provider env vars are set
    for provider in PROVIDERS.values():
        monkeypatch.delenv(provider.env_var, raising=False)

    # No provider env var set — phase should be 'detected' (not 'ready')
    resp = client.get("/distro/status")
    assert resp.status_code == 200

    data = resp.json()
    assert data["phase"] == "detected"


def test_run_command_returns_empty_on_missing_executable():
    """_run_command returns '' when the command executable does not exist."""
    import asyncio

    from distro_plugin.routes import _run_command

    result = asyncio.run(_run_command("__nonexistent_command_xyz__", "--version"))
    assert result == ""
