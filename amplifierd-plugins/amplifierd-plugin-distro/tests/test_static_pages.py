"""Tests for static HTML page endpoints serving real files."""

from __future__ import annotations

from pathlib import Path


def test_get_setup_serves_wizard_html(client):
    """GET /distro/setup serves HTML with 200 and contains 'wizard' or 'setup'."""
    resp = client.get("/distro/setup")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    body = resp.text.lower()
    assert "wizard" in body or "setup" in body


def test_get_settings_serves_settings_html(client):
    """GET /distro/settings serves HTML with 200 and contains 'settings'."""
    resp = client.get("/distro/settings")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "settings" in resp.text.lower()


# ---------------------------------------------------------------------------
# Static asset routes
# ---------------------------------------------------------------------------


def test_favicon_svg_served(client):
    """GET /favicon.svg returns 200."""
    resp = client.get("/favicon.svg")
    assert resp.status_code == 200


def test_static_theme_init_js_served(client):
    """GET /static/theme-init.js returns 200."""
    resp = client.get("/static/theme-init.js")
    assert resp.status_code == 200


def test_static_amplifier_theme_css_served(client):
    """GET /static/amplifier-theme.css returns 200."""
    resp = client.get("/static/amplifier-theme.css")
    assert resp.status_code == 200


def test_static_feedback_widget_js_served(client):
    """GET /static/feedback-widget.js returns 200."""
    resp = client.get("/static/feedback-widget.js")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# API path correctness in HTML files
# ---------------------------------------------------------------------------


_STATIC_DIR = Path(__file__).parent.parent / "src" / "distro_plugin" / "static"


def test_wizard_html_uses_distro_api():
    """wizard.html must declare const API = '/distro', not the old /apps/install-wizard path."""
    content = (_STATIC_DIR / "wizard.html").read_text()
    assert "const API = '/distro'" in content
    assert "/apps/install-wizard" not in content


def test_settings_html_uses_distro_api():
    """settings.html must declare const API = '/distro', not the old /apps/settings path."""
    content = (_STATIC_DIR / "settings.html").read_text()
    assert "const API = '/distro'" in content
    assert "/apps/settings" not in content


def test_settings_html_detect_endpoint():
    """settings.html must call /distro/detect, not /apps/install-wizard/detect."""
    content = (_STATIC_DIR / "settings.html").read_text()
    assert "fetch('/distro/detect')" in content
    assert "/apps/install-wizard/detect" not in content


def test_settings_html_wizard_link():
    """settings.html footer must link to /distro/setup, not /apps/install-wizard/."""
    content = (_STATIC_DIR / "settings.html").read_text()
    assert 'href="/distro/setup"' in content
    assert 'href="/apps/install-wizard/"' not in content
