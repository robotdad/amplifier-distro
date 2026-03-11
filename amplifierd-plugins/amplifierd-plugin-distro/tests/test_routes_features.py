"""Tests for provider & feature management endpoints.

Covers: GET /distro/providers, POST /distro/provider,
GET /distro/modules, POST /distro/features, POST /distro/tier.
"""

from __future__ import annotations

from distro_plugin.features import FEATURES, features_for_tier
from distro_plugin.overlay import get_includes


def test_get_providers_returns_list_with_id_fields(client):
    """GET /distro/providers returns a providers list where each entry has an 'id' field."""
    resp = client.get("/distro/providers")
    assert resp.status_code == 200

    data = resp.json()
    assert "providers" in data
    providers = data["providers"]
    assert isinstance(providers, list)
    assert len(providers) > 0

    for p in providers:
        assert "id" in p, f"Provider entry missing 'id': {p}"


def test_get_modules_returns_list_with_id_and_enabled(client):
    """GET /distro/modules returns a modules list where each entry has 'id' and 'enabled' fields."""
    resp = client.get("/distro/modules")
    assert resp.status_code == 200

    data = resp.json()
    assert "modules" in data
    modules = data["modules"]
    assert isinstance(modules, list)
    assert len(modules) > 0

    for m in modules:
        assert "id" in m, f"Module entry missing 'id': {m}"
        assert "enabled" in m, f"Module entry missing 'enabled': {m}"


def test_post_features_unknown_feature_returns_400(client):
    """POST /distro/features with an unknown feature_id returns 400."""
    resp = client.post(
        "/distro/features",
        json={"feature_id": "nonexistent-feature", "enabled": True},
    )
    assert resp.status_code == 400


def test_post_features_enable_succeeds(settings, client):
    """POST /distro/features with enabled=True adds the feature includes to the overlay."""
    resp = client.post(
        "/distro/features",
        json={"feature_id": "dev-memory", "enabled": True},
    )
    assert resp.status_code == 200

    data = resp.json()
    # Response is now the full status dict (phase/provider/features).
    assert "phase" in data

    # Verify the feature is now enabled (its includes are in the overlay)
    current = set(get_includes(settings))
    for inc in FEATURES["dev-memory"].includes:
        assert inc in current


def test_post_features_disable_succeeds(settings, client):
    """POST /distro/features with enabled=False removes the feature includes from the overlay."""
    # First enable it
    client.post(
        "/distro/features",
        json={"feature_id": "dev-memory", "enabled": True},
    )

    # Then disable it
    resp = client.post(
        "/distro/features",
        json={"feature_id": "dev-memory", "enabled": False},
    )
    assert resp.status_code == 200

    data = resp.json()
    # Response is now the full status dict (phase/provider/features).
    assert "phase" in data

    # Verify the feature is now disabled (its includes are not in the overlay)
    current = set(get_includes(settings))
    for inc in FEATURES["dev-memory"].includes:
        assert inc not in current


def test_post_tier_enables_tier1_features(settings, client):
    """POST /distro/tier with tier=1 enables all tier-1 features."""
    resp = client.post("/distro/tier", json={"tier": 1})
    assert resp.status_code == 200

    data = resp.json()
    assert data.get("status") == "ok"

    # Verify tier-1 features are enabled
    current = set(get_includes(settings))
    tier1_ids = features_for_tier(1)
    assert len(tier1_ids) > 0

    for fid in tier1_ids:
        feat = FEATURES[fid]
        for inc in feat.includes:
            assert inc in current, f"Expected include for {fid} in overlay: {inc}"


def test_post_provider_with_key_detects_and_registers(settings, client, monkeypatch):
    """POST /distro/provider with a valid API key detects provider and registers it."""
    # Clear any existing key so we start clean
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    resp = client.post(
        "/distro/provider",
        json={"api_key": "sk-ant-test-key-12345"},
    )
    assert resp.status_code == 200

    data = resp.json()
    assert data.get("status") == "ok"
    assert data.get("provider") == "anthropic"


def test_post_provider_bad_key_returns_400(client):
    """POST /distro/provider with an unrecognizable key returns 400."""
    resp = client.post(
        "/distro/provider",
        json={"api_key": "bad-unknown-key-format"},
    )
    assert resp.status_code == 400


def test_post_features_returns_full_status_dict(settings, client):
    """POST /distro/features returns full status response (phase/provider/features), not a stub."""
    resp = client.post(
        "/distro/features",
        json={"feature_id": "dev-memory", "enabled": True},
    )
    assert resp.status_code == 200

    data = resp.json()
    # Must return full status, not the minimal {"status": "ok", ...} stub
    assert "phase" in data, "Expected 'phase' in full status response"
    assert "provider" in data, "Expected 'provider' in full status response"
    assert "features" in data, "Expected 'features' in full status response"

    # features should be a dict with enabled state reflected
    assert isinstance(data["features"], dict)
    assert data["features"]["dev-memory"]["enabled"] is True
