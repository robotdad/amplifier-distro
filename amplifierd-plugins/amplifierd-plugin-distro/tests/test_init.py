"""Tests for distro_plugin.__init__ — create_router bundle registry integration."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _make_overlay(tmp_path):
    """Create a minimal overlay bundle.yaml under tmp_path."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "bundle.yaml").write_text("bundle:\n  name: test\nincludes: []\n")
    return bundle_dir


def test_create_router_registers_overlay_when_exists(tmp_path):
    """register() is called with {"distro": overlay_dir} when overlay exists."""
    from distro_plugin import create_router

    _make_overlay(tmp_path)

    mock_registry = MagicMock()
    state = SimpleNamespace(bundle_registry=mock_registry)

    with patch.dict(os.environ, {"DISTRO_PLUGIN_DISTRO_HOME": str(tmp_path)}):
        create_router(state)

    expected_overlay_dir = str(tmp_path / "bundle")
    mock_registry.register.assert_called_once_with({"distro": expected_overlay_dir})


def test_create_router_skips_registration_when_no_overlay(tmp_path):
    """register() is NOT called when no overlay bundle.yaml exists."""
    from distro_plugin import create_router

    # tmp_path exists but has no bundle/bundle.yaml
    mock_registry = MagicMock()
    state = SimpleNamespace(bundle_registry=mock_registry)

    with patch.dict(os.environ, {"DISTRO_PLUGIN_DISTRO_HOME": str(tmp_path)}):
        create_router(state)

    mock_registry.register.assert_not_called()


def test_create_router_handles_no_bundle_registry(tmp_path):
    """No error raised when state has no bundle_registry attribute."""
    from distro_plugin import create_router

    _make_overlay(tmp_path)

    # state has no bundle_registry attribute at all
    state = SimpleNamespace()

    with patch.dict(os.environ, {"DISTRO_PLUGIN_DISTRO_HOME": str(tmp_path)}):
        create_router(state)  # should not raise
