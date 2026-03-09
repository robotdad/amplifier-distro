"""Tests for distro_plugin.overlay — bundle include management."""

from __future__ import annotations

import yaml

from distro_plugin.config import DistroPluginSettings
from distro_plugin.overlay import (
    _DISTRO_BUNDLE_URI,
    add_include,
    get_includes,
    overlay_exists,
    read_overlay,
    remove_include,
)


def _overlay_path(settings: DistroPluginSettings):
    return settings.distro_home / "bundle" / "bundle.yaml"


def _write(settings: DistroPluginSettings, data: dict):
    path = _overlay_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


# -- overlay_exists ----------------------------------------------------------


def test_overlay_exists_false_when_missing(settings):
    """overlay_exists returns False when no overlay file exists."""
    assert overlay_exists(settings) is False


def test_overlay_exists_true_when_present(settings):
    """overlay_exists returns True when the overlay file exists."""
    _write(settings, {"includes": []})
    assert overlay_exists(settings) is True


# -- read_overlay ------------------------------------------------------------


def test_read_overlay_returns_empty_when_missing(settings):
    """read_overlay returns {} when no overlay file exists."""
    assert read_overlay(settings) == {}


def test_read_overlay_parses_yaml(settings):
    """read_overlay parses the YAML file and returns its contents."""
    data = {
        "bundle": {"name": "test", "version": "0.1.0"},
        "includes": [{"bundle": "git+https://example.com/repo@main"}],
    }
    _write(settings, data)
    assert read_overlay(settings) == data


# -- get_includes ------------------------------------------------------------


def test_get_includes_extracts_uris_dict_and_string(settings):
    """get_includes handles both dict and bare string include entries."""
    data = {
        "includes": [
            {"bundle": "git+https://example.com/a@main"},
            "git+https://example.com/b@main",
        ],
    }
    _write(settings, data)
    assert get_includes(settings) == [
        "git+https://example.com/a@main",
        "git+https://example.com/b@main",
    ]


def test_get_includes_empty_when_no_overlay(settings):
    """get_includes returns [] when no overlay file exists."""
    assert get_includes(settings) == []


# -- add_include -------------------------------------------------------------


def test_add_include_creates_overlay_when_missing(settings):
    """add_include bootstraps a new overlay with the distro bundle URI."""
    add_include(settings, "git+https://example.com/extra@main")
    assert overlay_exists(settings) is True
    uris = get_includes(settings)
    assert _DISTRO_BUNDLE_URI in uris
    assert "git+https://example.com/extra@main" in uris
    # Distro bundle should come first
    assert uris.index(_DISTRO_BUNDLE_URI) < uris.index(
        "git+https://example.com/extra@main"
    )


def test_add_include_idempotent(settings):
    """add_include does not duplicate an already-present URI."""
    add_include(settings, "git+https://example.com/extra@main")
    add_include(settings, "git+https://example.com/extra@main")
    uris = get_includes(settings)
    assert uris.count("git+https://example.com/extra@main") == 1


def test_add_include_appends_to_existing(settings):
    """add_include appends a new URI to an existing overlay."""
    _write(
        settings,
        {
            "bundle": {"name": "test", "version": "0.1.0"},
            "includes": [{"bundle": _DISTRO_BUNDLE_URI}],
        },
    )
    add_include(settings, "git+https://example.com/new@main")
    uris = get_includes(settings)
    assert uris == [_DISTRO_BUNDLE_URI, "git+https://example.com/new@main"]


# -- remove_include ----------------------------------------------------------


def test_remove_include_works(settings):
    """remove_include removes the specified URI from the overlay."""
    _write(
        settings,
        {
            "includes": [
                {"bundle": _DISTRO_BUNDLE_URI},
                {"bundle": "git+https://example.com/removeme@main"},
            ],
        },
    )
    remove_include(settings, "git+https://example.com/removeme@main")
    assert get_includes(settings) == [_DISTRO_BUNDLE_URI]


def test_remove_include_noop_when_no_overlay(settings):
    """remove_include is a no-op when no overlay exists."""
    # Should not raise
    remove_include(settings, "git+https://example.com/whatever@main")
    assert overlay_exists(settings) is False
