"""Tests for distro_plugin.overlay — bundle include management."""

from __future__ import annotations

import pytest
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


# -- migrate_overlay ---------------------------------------------------------


def test_migrate_overlay_replaces_old_uri_with_current(settings):
    """migrate_overlay replaces a moved URI with the current equivalent in-place."""
    from distro_plugin.overlay import (
        _URI_REPLACEMENTS,
        migrate_overlay,
    )

    if not _URI_REPLACEMENTS:
        pytest.skip("No URI replacements defined")

    old_uri = next(iter(_URI_REPLACEMENTS))
    new_uri = _URI_REPLACEMENTS[old_uri]

    _write(
        settings,
        {
            "bundle": {"name": "test", "version": "0.1.0"},
            "includes": [
                {"bundle": old_uri},
                {"bundle": "git+https://example.com/extra@main"},
            ],
        },
    )

    migrate_overlay(settings)

    uris = get_includes(settings)
    assert old_uri not in uris, f"Stale URI {old_uri!r} should have been replaced"
    assert new_uri in uris, f"New URI {new_uri!r} should be present after migration"
    # Extra include should be preserved
    assert "git+https://example.com/extra@main" in uris


def test_migrate_overlay_removes_stale_uris(settings):
    """migrate_overlay removes URIs listed in _STALE_URIS."""
    from distro_plugin.overlay import (
        _DISTRO_BUNDLE_URI,
        _STALE_URIS,
        migrate_overlay,
    )

    if not _STALE_URIS:
        pytest.skip("No stale URIs defined")

    stale_uri = _STALE_URIS[0]
    _write(
        settings,
        {
            "bundle": {"name": "test", "version": "0.1.0"},
            "includes": [
                {"bundle": _DISTRO_BUNDLE_URI},
                {"bundle": stale_uri},
            ],
        },
    )

    migrate_overlay(settings)

    uris = get_includes(settings)
    assert stale_uri not in uris, f"Stale URI {stale_uri!r} should have been removed"


def test_migrate_overlay_ensures_distro_bundle_uri_at_position_0(settings):
    """migrate_overlay ensures _DISTRO_BUNDLE_URI is present and at position 0."""
    from distro_plugin.overlay import _DISTRO_BUNDLE_URI, migrate_overlay

    # Overlay without the distro bundle URI
    _write(
        settings,
        {
            "bundle": {"name": "test", "version": "0.1.0"},
            "includes": [
                {"bundle": "git+https://example.com/some-feature@main"},
            ],
        },
    )

    migrate_overlay(settings)

    uris = get_includes(settings)
    assert _DISTRO_BUNDLE_URI in uris
    assert uris[0] == _DISTRO_BUNDLE_URI, "Distro bundle URI should be at position 0"


def test_migrate_overlay_noop_when_no_overlay(settings):
    """migrate_overlay is a no-op when no overlay file exists."""
    from distro_plugin.overlay import migrate_overlay

    # Should not raise, overlay should still not exist
    migrate_overlay(settings)
    assert overlay_exists(settings) is False


def test_migrate_overlay_noop_when_already_current(settings):
    """migrate_overlay does not write when no migration is needed."""
    from distro_plugin.overlay import _DISTRO_BUNDLE_URI, migrate_overlay

    path = _overlay_path(settings)
    _write(
        settings,
        {
            "bundle": {"name": "test", "version": "0.1.0"},
            "includes": [
                {"bundle": _DISTRO_BUNDLE_URI},
                {"bundle": "git+https://example.com/feature@main"},
            ],
        },
    )

    mtime_before = path.stat().st_mtime
    migrate_overlay(settings)
    mtime_after = path.stat().st_mtime

    assert mtime_before == mtime_after, (
        "File should not be rewritten if already current"
    )


# -- reload wiring -----------------------------------------------------------


def test_add_include_triggers_reload_when_app_provided(settings, monkeypatch):
    """add_include calls request_reload when app is provided."""
    import distro_plugin.reload as reload_mod

    mock_app = object()
    calls = []

    monkeypatch.setattr(reload_mod, "request_reload", lambda app: calls.append(app))

    add_include(settings, "git+https://example.com/feature@main", app=mock_app)

    assert calls == [mock_app], "request_reload should be called with the app"


def test_add_include_works_without_app(settings):
    """add_include works normally when app is not provided (backward compat)."""
    # Should not raise — no reload triggered
    add_include(settings, "git+https://example.com/feature@main")
    assert "git+https://example.com/feature@main" in get_includes(settings)


def test_remove_include_triggers_reload_when_app_provided(settings, monkeypatch):
    """remove_include calls request_reload when app is provided."""
    import yaml

    import distro_plugin.reload as reload_mod

    path = settings.distro_home / "bundle" / "bundle.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(
            {
                "includes": [
                    {"bundle": "git+https://example.com/distro@main"},
                    {"bundle": "git+https://example.com/feature@main"},
                ]
            },
            default_flow_style=False,
            sort_keys=False,
        )
    )

    mock_app = object()
    calls = []

    monkeypatch.setattr(reload_mod, "request_reload", lambda app: calls.append(app))

    remove_include(settings, "git+https://example.com/feature@main", app=mock_app)

    assert calls == [mock_app], "request_reload should be called with the app"


def test_add_include_on_existing_overlay_with_stale_uri_migrates(settings):
    """add_include migrates stale URIs in an existing overlay before adding."""
    from distro_plugin.overlay import (
        _URI_REPLACEMENTS,
        add_include,
    )

    if not _URI_REPLACEMENTS:
        pytest.skip("No URI replacements defined")

    old_uri = next(iter(_URI_REPLACEMENTS))
    new_uri = _URI_REPLACEMENTS[old_uri]

    _write(
        settings,
        {
            "bundle": {"name": "test", "version": "0.1.0"},
            "includes": [{"bundle": old_uri}],
        },
    )

    add_include(settings, "git+https://example.com/new-feature@main")

    uris = get_includes(settings)
    assert old_uri not in uris, "Old URI should have been replaced during add_include"
    assert new_uri in uris, "New URI should be present after migration in add_include"
    assert "git+https://example.com/new-feature@main" in uris
