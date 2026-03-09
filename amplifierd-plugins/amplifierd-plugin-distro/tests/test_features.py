"""Tests for distro_plugin.features — catalog, tiers, enabled detection."""

from __future__ import annotations

import yaml

from distro_plugin.config import DistroPluginSettings
from distro_plugin.features import (
    FEATURES,
    TIERS,
    Feature,
    features_for_tier,
    get_enabled_features,
)


def _overlay_path(settings: DistroPluginSettings):
    """Return the expected overlay bundle.yaml path for the given settings."""
    return settings.distro_home / "bundle" / "bundle.yaml"


def _write(settings: DistroPluginSettings, data: dict):
    """Write *data* as YAML to the overlay bundle path, creating parents."""
    path = _overlay_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False))


# -- FEATURES catalog --------------------------------------------------------


def test_catalog_has_expected_entries():
    """FEATURES catalog contains dev-memory and deliberate-dev."""
    assert "dev-memory" in FEATURES
    assert "deliberate-dev" in FEATURES


def test_feature_has_required_fields():
    """Each Feature has name, tier, includes, and category fields."""
    feature = FEATURES["dev-memory"]
    assert isinstance(feature, Feature)
    assert isinstance(feature.name, str) and feature.name
    assert isinstance(feature.tier, int)
    assert isinstance(feature.includes, tuple) and len(feature.includes) > 0
    assert isinstance(feature.category, str) and feature.category


# -- features_for_tier -------------------------------------------------------


def test_features_for_tier_0_returns_empty():
    """features_for_tier(0) returns an empty list."""
    assert features_for_tier(0) == []


def test_features_for_tier_negative_returns_empty():
    """features_for_tier(-1) returns an empty list."""
    assert features_for_tier(-1) == []


def test_features_for_tier_1_returns_tier_1_only():
    """features_for_tier(1) returns only tier-1 feature IDs."""
    result = features_for_tier(1)
    assert result == list(TIERS[1])
    # Should not contain any tier-2 features
    for fid in TIERS[2]:
        assert fid not in result


def test_features_for_tier_2_includes_tier_1_and_tier_2():
    """features_for_tier(2) accumulates tier-1 and tier-2 features."""
    result = features_for_tier(2)
    assert result == list(TIERS[1]) + list(TIERS[2])


# -- get_enabled_features ----------------------------------------------------


def test_get_enabled_features_empty_when_no_overlay(settings):
    """get_enabled_features returns [] when overlay has no includes."""
    assert get_enabled_features(settings) == []


def test_get_enabled_features_detects_by_uri_matching(settings):
    """get_enabled_features detects enabled features by URI matching."""
    # Enable dev-memory by putting its includes into the overlay
    dev_memory = FEATURES["dev-memory"]
    _write(
        settings,
        {
            "bundle": {"name": "test", "version": "0.1.0"},
            "includes": [{"bundle": uri} for uri in dev_memory.includes],
        },
    )
    enabled = get_enabled_features(settings)
    assert "dev-memory" in enabled


def test_get_enabled_features_ignores_unrelated_uris(settings):
    """get_enabled_features ignores URIs that don't match any feature."""
    _write(
        settings,
        {
            "bundle": {"name": "test", "version": "0.1.0"},
            "includes": [
                {"bundle": "git+https://example.com/unrelated@main"},
            ],
        },
    )
    enabled = get_enabled_features(settings)
    assert enabled == []
