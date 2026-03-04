"""Tests for the static login.html page.

Validates that login.html exists, is valid HTML, and contains all
required elements per the spec: heading, subtitle, form inputs,
submit button, error display, theme CSS link, and fetch-based JS.
"""

from pathlib import Path

import pytest

LOGIN_HTML = (
    Path(__file__).parent.parent
    / "src"
    / "amplifier_distro"
    / "server"
    / "static"
    / "login.html"
)


@pytest.fixture
def html_content() -> str:
    """Read login.html content. Fails fast if file missing."""
    assert LOGIN_HTML.exists(), f"login.html not found at {LOGIN_HTML}"
    return LOGIN_HTML.read_text()


class TestLoginHtmlExists:
    """File must exist at the expected path."""

    def test_file_exists(self):
        assert LOGIN_HTML.exists()


class TestLoginHtmlStructure:
    """Validate HTML structure and required elements."""

    def test_is_valid_html_document(self, html_content):
        """Contains DOCTYPE and basic HTML structure."""
        assert "<!DOCTYPE html>" in html_content or "<!doctype html>" in html_content
        assert "<html" in html_content
        assert "</html>" in html_content
        assert "<head>" in html_content or "<head " in html_content
        assert "<body>" in html_content or "<body " in html_content

    def test_contains_amplifier_distro_heading(self, html_content):
        """Has 'Amplifier Distro' text (the heading)."""
        assert "Amplifier Distro" in html_content

    def test_contains_subtitle(self, html_content):
        """Has 'Sign in with your system account' subtitle."""
        assert "Sign in with your system account" in html_content

    def test_references_amplifier_theme_css(self, html_content):
        """Links to /static/amplifier-theme.css."""
        assert "amplifier-theme.css" in html_content

    def test_contains_username_input(self, html_content):
        """Has a username input with autocomplete='username' and autofocus."""
        lower = html_content.lower()
        assert 'name="username"' in lower
        assert "autocomplete" in lower
        assert "username" in lower
        assert "autofocus" in lower

    def test_contains_password_input(self, html_content):
        """Has a password input with type=password and autocomplete."""
        lower = html_content.lower()
        assert 'name="password"' in lower
        assert 'type="password"' in lower
        assert "current-password" in lower

    def test_contains_sign_in_button(self, html_content):
        """Has a 'Sign in' submit button."""
        assert "Sign in" in html_content
        lower = html_content.lower()
        assert "<button" in lower

    def test_contains_error_msg_div(self, html_content):
        """Has an error display element with .error-msg class."""
        assert "error-msg" in html_content


class TestLoginHtmlJavaScript:
    """Validate the fetch-based form submission JS."""

    def test_contains_fetch_to_login(self, html_content):
        """JS intercepts form submit with fetch('/login', ...)."""
        assert "fetch(" in html_content
        assert "/login" in html_content
        assert "POST" in html_content

    def test_uses_formdata(self, html_content):
        """JS sends FormData in the fetch body."""
        assert "FormData" in html_content

    def test_handles_redirect(self, html_content):
        """JS handles 303 redirect (checks response.redirected or status)."""
        # The JS should redirect to '/' on success
        lower = html_content.lower()
        assert "redirected" in lower or "303" in lower or "location" in lower

    def test_handles_401_error(self, html_content):
        """JS handles 401 responses to show an error message."""
        assert "401" in html_content

    def test_handles_network_errors(self, html_content):
        """JS has a catch block for network errors."""
        assert "catch" in html_content


class TestLoginHtmlStyling:
    """Validate key styling requirements."""

    def test_login_card_max_width(self, html_content):
        """Login card has max-width: 380px."""
        assert "380px" in html_content

    def test_has_border_radius(self, html_content):
        """Card has rounded corners (border-radius)."""
        assert "border-radius" in html_content
