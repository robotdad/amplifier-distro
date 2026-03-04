"""Tests for isSecureContext guard in voice app static/index.html

Verifies that the Preact voice app includes a browser-side secure context
check that prevents getUserMedia from being called over plain HTTP.

Acceptance criteria:
  1. HTML contains 'isSecureContext'
  2. Contains 'secure connection' or 'secure context' message
  3. Contains '--tls auto' suggestion
  4. isSecureContext check appears before getUserMedia in the source
"""

from __future__ import annotations

from pathlib import Path

# Path to static/index.html (resolved relative to this test file)
_STATIC_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "amplifier_distro"
    / "server"
    / "apps"
    / "voice"
    / "static"
)
INDEX_HTML = _STATIC_DIR / "index.html"


class TestSecureContextGuard:
    """Tests for the isSecureContext guard in the connect() function."""

    def setup_method(self) -> None:
        self.html = INDEX_HTML.read_text(encoding="utf-8")

    # --- Core guard logic ---

    def test_html_contains_is_secure_context(self) -> None:
        assert "isSecureContext" in self.html, (
            "index.html must contain 'isSecureContext' check"
        )

    def test_secure_context_check_before_get_user_media(self) -> None:
        """isSecureContext check must appear before getUserMedia in source."""
        secure_pos = self.html.find("isSecureContext")
        media_pos = self.html.find("getUserMedia")
        assert secure_pos != -1, "isSecureContext not found in HTML"
        assert media_pos != -1, "getUserMedia not found in HTML"
        assert secure_pos < media_pos, (
            "isSecureContext check must appear before getUserMedia call "
            f"(found isSecureContext at {secure_pos}, getUserMedia at {media_pos})"
        )

    # --- User-facing error banner ---

    def test_contains_secure_connection_message(self) -> None:
        lower = self.html.lower()
        assert "secure connection" in lower or "secure context" in lower, (
            "index.html must contain 'secure connection' or 'secure context' "
            "message for the user-facing error banner"
        )

    def test_contains_tls_auto_suggestion(self) -> None:
        assert "--tls auto" in self.html, (
            "index.html must contain '--tls auto' as a remediation suggestion"
        )

    # --- Banner styling and remediation steps ---

    def test_secure_context_error_state_variable(self) -> None:
        assert "secureContextError" in self.html, (
            "index.html must declare a secureContextError state variable"
        )

    def test_banner_mentions_microphone_over_http(self) -> None:
        lower = self.html.lower()
        assert "microphone" in lower and "http" in lower, (
            "Secure context error banner must explain that microphone "
            "access is unavailable over plain HTTP"
        )

    def test_banner_mentions_tailscale(self) -> None:
        lower = self.html.lower()
        assert "tailscale" in lower, (
            "Secure context error banner must mention Tailscale hostname "
            "as a remediation option"
        )

    def test_banner_mentions_localhost(self) -> None:
        assert "localhost" in self.html, (
            "Secure context error banner must mention localhost as a remediation option"
        )

    def test_banner_dark_theme_background(self) -> None:
        assert "#1a1a2e" in self.html, (
            "Secure context error banner must use dark theme background #1a1a2e"
        )

    def test_banner_dark_theme_border(self) -> None:
        assert "#2a2a4a" in self.html, (
            "Secure context error banner must use dark theme border #2a2a4a"
        )
