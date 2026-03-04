# Secure Remote Access Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Fix voice app remote access failure (issue #68) by adding browser secure context detection, native TLS, PAM authentication, and dynamic CSRF origin checking.

**Architecture:** Four independent, composable layers — each delivers value alone. A browser-side guard catches the problem immediately. Native TLS via uvicorn enables HTTPS (Tailscale certs or self-signed). PAM auth protects remote access on Linux. A dynamic CSRF origin allow-list replaces the hardcoded localhost check. Plain `amp-distro serve` behaves exactly as today.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, Click 8, `python-pam`, `itsdangerous`, Preact+htm (no build step)

**Design doc:** `docs/plans/2026-03-03-secure-remote-access-design.md`
**Branch:** `feat/secure-remote-access-68`

---

## Phase 1: Foundation

### Task 1: Add TLS and Auth Settings Dataclasses

**Files:**
- Modify: `distro-server/src/amplifier_distro/distro_settings.py`
- Modify: `distro-server/src/amplifier_distro/conventions.py`
- Test: `distro-server/tests/test_settings_tls_auth.py`

**Context:** Settings use `@dataclass` classes nested inside `DistroSettings`. Look at `VoiceSettings` (line 73) and `WatchdogSettings` (line 85) in `distro_settings.py` for the exact pattern. New sections get added as fields on `DistroSettings` (line 94). Conventions constants live in `conventions.py` — see `DISTRO_HOME` (line 23) and `DISTRO_SESSIONS_DIR` (line 29) for the naming pattern.

**Step 1: Write the failing test**

Create `distro-server/tests/test_settings_tls_auth.py`:

```python
"""Tests for TLS and auth settings dataclasses.

Verifies the new TlsSettings, AuthSettings, and ServerSettings
dataclasses exist, have correct defaults, and round-trip through
load/save correctly.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from amplifier_distro import conventions
from amplifier_distro.distro_settings import (
    AuthSettings,
    DistroSettings,
    ServerSettings,
    TlsSettings,
    load,
    save,
)


class TestTlsSettingsDefaults:
    """TlsSettings has correct default values."""

    def test_mode_defaults_to_off(self):
        s = TlsSettings()
        assert s.mode == "off"

    def test_certfile_defaults_to_empty(self):
        s = TlsSettings()
        assert s.certfile == ""

    def test_keyfile_defaults_to_empty(self):
        s = TlsSettings()
        assert s.keyfile == ""


class TestAuthSettingsDefaults:
    """AuthSettings has correct default values."""

    def test_enabled_defaults_to_true(self):
        s = AuthSettings()
        assert s.enabled is True

    def test_session_timeout_defaults_to_30_days(self):
        s = AuthSettings()
        assert s.session_timeout == 2592000


class TestServerSettingsDefaults:
    """ServerSettings nests TLS and auth with correct defaults."""

    def test_tls_is_tls_settings(self):
        s = ServerSettings()
        assert isinstance(s.tls, TlsSettings)

    def test_auth_is_auth_settings(self):
        s = ServerSettings()
        assert isinstance(s.auth, AuthSettings)

    def test_allowed_origins_defaults_to_empty_list(self):
        s = ServerSettings()
        assert s.allowed_origins == []


class TestDistroSettingsHasServer:
    """DistroSettings root object includes the new server section."""

    def test_server_field_exists(self):
        s = DistroSettings()
        assert isinstance(s.server, ServerSettings)

    def test_server_tls_mode_default(self):
        s = DistroSettings()
        assert s.server.tls.mode == "off"


class TestSettingsRoundTrip:
    """Settings survive save/load cycle with new server fields."""

    def test_round_trip_preserves_tls_mode(self, tmp_path, monkeypatch):
        settings_file = tmp_path / "settings.yaml"
        monkeypatch.setattr(
            "amplifier_distro.distro_settings._settings_path",
            lambda: settings_file,
        )

        s = DistroSettings()
        s.server.tls.mode = "auto"
        s.server.auth.session_timeout = 86400
        s.server.allowed_origins = ["https://my-proxy.local"]
        save(s)

        loaded = load()
        assert loaded.server.tls.mode == "auto"
        assert loaded.server.auth.session_timeout == 86400
        assert loaded.server.allowed_origins == ["https://my-proxy.local"]


class TestCertsDir:
    """conventions.DISTRO_CERTS_DIR constant exists."""

    def test_certs_dir_is_under_distro_home(self):
        assert "certs" in conventions.DISTRO_CERTS_DIR
        assert conventions.DISTRO_HOME in conventions.DISTRO_CERTS_DIR
```

**Step 2: Run test to verify it fails**

```bash
cd distro-server && python -m pytest tests/test_settings_tls_auth.py -v
```
Expected: FAIL — `ImportError: cannot import name 'TlsSettings'`

**Step 3: Add the DISTRO_CERTS_DIR convention**

In `distro-server/src/amplifier_distro/conventions.py`, add after the `DISTRO_SESSIONS_DIR` line (line 29):

```python
DISTRO_CERTS_DIR = f"{DISTRO_HOME}/certs"  # TLS certificates (Tailscale or self-signed)
```

**Step 4: Add the settings dataclasses**

In `distro-server/src/amplifier_distro/distro_settings.py`, add these three new dataclasses after `WatchdogSettings` (after line 91) and before `DistroSettings`:

```python
@dataclass
class TlsSettings:
    """TLS configuration for the server."""

    mode: str = "off"  # "auto" | "off" | "manual"
    certfile: str = ""  # path for manual mode
    keyfile: str = ""  # path for manual mode


@dataclass
class AuthSettings:
    """PAM authentication configuration."""

    enabled: bool = True  # enable PAM auth (Linux/WSL only, requires TLS)
    session_timeout: int = 2592000  # 30 days in seconds


@dataclass
class ServerSettings:
    """Server-level settings (TLS, auth, CSRF origins)."""

    tls: TlsSettings = field(default_factory=TlsSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)
    allowed_origins: list[str] = field(default_factory=list)
```

Then add a `server` field to `DistroSettings` (after the `watchdog` field on line 102):

```python
    server: ServerSettings = field(default_factory=ServerSettings)
```

**Step 5: Run test to verify it passes**

```bash
cd distro-server && python -m pytest tests/test_settings_tls_auth.py -v
```
Expected: All PASS

**Step 6: Run existing settings tests to check for regressions**

```bash
cd distro-server && python -m pytest tests/ -k "settings or conventions" -v
```
Expected: All PASS (no regressions)

**Step 7: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: add TLS and auth settings dataclasses

Add TlsSettings, AuthSettings, ServerSettings to distro_settings.py.
Add DISTRO_CERTS_DIR constant to conventions.py.
Wire ServerSettings into DistroSettings root.
Defaults: TLS off, auth enabled (but only activates when TLS+Linux).

Ref: #68"
```

---

### Task 2: Add Dependencies

**Files:**
- Modify: `distro-server/pyproject.toml`

**Context:** Dependencies are listed in `pyproject.toml` under `[project] dependencies` (line 14-29). There is no separate requirements.txt.

**Step 1: Add python-pam and itsdangerous to dependencies**

In `distro-server/pyproject.toml`, add these two lines to the `dependencies` list (after line 28, before the closing `]`):

```
    "python-pam>=2.0.0",
    "itsdangerous>=2.0",
```

**Step 2: Install the updated dependencies**

```bash
cd distro-server && pip install -e ".[all]"
```
Expected: Both packages install successfully

**Step 3: Verify imports work**

```bash
cd distro-server && python -c "import pam; print('pam ok')" && python -c "import itsdangerous; print('itsdangerous ok')"
```
Expected: Both print "ok"

**Step 4: Commit**

```bash
cd distro-server && git add pyproject.toml && git commit -m "feat: add python-pam and itsdangerous dependencies

python-pam: PAM authentication for Linux/WSL remote access.
itsdangerous: Signed session cookies for browser auth.

Ref: #68"
```

---

### Task 3: Build Shared Allowed-Origins Utility

**Files:**
- Create: `distro-server/src/amplifier_distro/server/origins.py`
- Test: `distro-server/tests/test_origins.py`

**Context:** The current `_check_origin` in `voice/__init__.py` (line 133-139) hardcodes `localhost`/`127.0.0.1`. This new module builds a dynamic allow-list from server state that any app can use. `tailscale.get_dns_name()` returns the Tailscale hostname or `None`. The test pattern to follow is `test_tailscale.py` — class-based, `unittest.mock.patch`, plain `assert`.

**Step 1: Write the failing test**

Create `distro-server/tests/test_origins.py`:

```python
"""Tests for amplifier_distro.server.origins module.

Verifies the dynamic CSRF origin allow-list is built correctly
from Tailscale status, hostname, and explicit config.
"""

from __future__ import annotations

from unittest.mock import patch

from amplifier_distro.server.origins import build_allowed_origins, is_origin_allowed


class TestBuildAllowedOrigins:
    """Tests for build_allowed_origins()."""

    def test_always_includes_localhost(self):
        origins = build_allowed_origins()
        assert "localhost" in origins
        assert "127.0.0.1" in origins

    def test_includes_tailscale_dns_when_available(self):
        with patch(
            "amplifier_distro.server.origins.get_dns_name",
            return_value="mybox.tail1234.ts.net",
        ):
            origins = build_allowed_origins()
            assert "mybox.tail1234.ts.net" in origins

    def test_excludes_tailscale_when_unavailable(self):
        with patch(
            "amplifier_distro.server.origins.get_dns_name",
            return_value=None,
        ):
            origins = build_allowed_origins()
            # Should still have localhost but no Tailscale entry
            assert "localhost" in origins
            assert len([o for o in origins if "ts.net" in o]) == 0

    def test_includes_system_hostname(self):
        with patch(
            "amplifier_distro.server.origins.socket.gethostname",
            return_value="devbox",
        ):
            origins = build_allowed_origins()
            assert "devbox" in origins

    def test_includes_extra_origins(self):
        origins = build_allowed_origins(extra=["https://my-proxy.local"])
        assert "https://my-proxy.local" in origins

    def test_deduplicates(self):
        origins = build_allowed_origins(extra=["localhost"])
        assert origins.count("localhost") == 1


class TestIsOriginAllowed:
    """Tests for is_origin_allowed()."""

    def test_none_origin_is_allowed(self):
        assert is_origin_allowed(None, {"localhost"}) is True

    def test_localhost_origin_is_allowed(self):
        allowed = {"localhost", "127.0.0.1"}
        assert is_origin_allowed("http://localhost:8400", allowed) is True

    def test_127_origin_is_allowed(self):
        allowed = {"localhost", "127.0.0.1"}
        assert is_origin_allowed("http://127.0.0.1:8400", allowed) is True

    def test_tailscale_origin_is_allowed(self):
        allowed = {"localhost", "127.0.0.1", "mybox.tail1234.ts.net"}
        assert is_origin_allowed("https://mybox.tail1234.ts.net", allowed) is True

    def test_evil_origin_is_rejected(self):
        allowed = {"localhost", "127.0.0.1"}
        assert is_origin_allowed("https://evil.example.com", allowed) is False

    def test_custom_proxy_origin_is_allowed(self):
        allowed = {"localhost", "127.0.0.1", "https://my-proxy.local"}
        assert is_origin_allowed("https://my-proxy.local", allowed) is True
```

**Step 2: Run test to verify it fails**

```bash
cd distro-server && python -m pytest tests/test_origins.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'amplifier_distro.server.origins'`

**Step 3: Implement the origins module**

Create `distro-server/src/amplifier_distro/server/origins.py`:

```python
"""Dynamic CSRF origin allow-list for amplifier-distro.

Builds a set of allowed origins from server state:
- Always: localhost, 127.0.0.1
- If Tailscale connected: the Tailscale DNS name
- System hostname
- Explicit extras from settings

Any app can use ``is_origin_allowed()`` to check incoming Origin headers.
"""

from __future__ import annotations

import logging
import socket

from amplifier_distro.tailscale import get_dns_name

logger = logging.getLogger(__name__)


def build_allowed_origins(extra: list[str] | None = None) -> list[str]:
    """Build the origin allow-list from server state.

    Args:
        extra: Additional origins from settings (escape hatch).

    Returns:
        Deduplicated list of allowed origin strings.
    """
    origins: list[str] = ["localhost", "127.0.0.1"]

    # Tailscale DNS name
    ts_dns = get_dns_name()
    if ts_dns:
        origins.append(ts_dns)

    # System hostname
    try:
        hostname = socket.gethostname()
        if hostname:
            origins.append(hostname)
    except OSError:
        pass

    # Explicit extras from config
    if extra:
        origins.extend(extra)

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for origin in origins:
        if origin not in seen:
            seen.add(origin)
            deduped.append(origin)

    return deduped


def is_origin_allowed(origin: str | None, allowed: set[str]) -> bool:
    """Check whether an Origin header value is in the allow-list.

    Args:
        origin: The Origin header value (None means no header = allowed).
        allowed: Set of allowed origin strings from ``build_allowed_origins()``.

    Returns:
        True if the origin is allowed, False otherwise.
    """
    if origin is None:
        return True  # No origin header = allow (same as current behavior)

    # Check if any allowed entry appears in the origin string.
    # This handles "http://localhost:8400" matching "localhost",
    # "https://mybox.ts.net" matching "mybox.ts.net", etc.
    return any(entry in origin for entry in allowed)
```

**Step 4: Run test to verify it passes**

```bash
cd distro-server && python -m pytest tests/test_origins.py -v
```
Expected: All PASS

**Step 5: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: add shared allowed-origins utility

New module server/origins.py builds a dynamic CSRF origin allow-list
from Tailscale DNS, system hostname, and explicit config. Replaces
the hardcoded localhost-only check. No wildcard, enumerate known-good only.

Ref: #68"
```

---

### Task 4: Wire Allowed Origins into Voice _check_origin

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/voice/__init__.py` (lines 133-139)
- Modify: `distro-server/tests/test_voice_routes.py` (TestCsrfProtection class, lines 263-302)
- Test: `distro-server/tests/test_origins.py` (already created)

**Context:** The current `_check_origin` function at line 133-139 in `voice/__init__.py` does a simple substring check for "localhost" or "127.0.0.1". We need to replace it with a call to `is_origin_allowed()`. The existing CSRF tests in `test_voice_routes.py` (lines 263-302) call `_check_origin` directly and via the `/events` endpoint — those must still pass. We also need to add a test that Tailscale origins are now allowed.

**Step 1: Write the new test for Tailscale origin**

Add a new test method to the `TestCsrfProtection` class in `distro-server/tests/test_voice_routes.py`. Add it after the `test_events_no_origin_allowed` method (after line 302):

```python
    async def test_events_tailscale_origin_allowed(self) -> None:
        """_check_origin allows Tailscale DNS origin when in allow-list."""
        from unittest.mock import patch

        from amplifier_distro.server.apps.voice import _check_origin

        with patch(
            "amplifier_distro.server.apps.voice._allowed_origins",
            {"localhost", "127.0.0.1", "mybox.tail1234.ts.net"},
        ):
            # Should complete without raising HTTPException
            await _check_origin(origin="https://mybox.tail1234.ts.net")
```

**Step 2: Run the new test to verify it fails**

```bash
cd distro-server && python -m pytest tests/test_voice_routes.py::TestCsrfProtection::test_events_tailscale_origin_allowed -v
```
Expected: FAIL — `_allowed_origins` does not exist yet

**Step 3: Update _check_origin to use the origins module**

In `distro-server/src/amplifier_distro/server/apps/voice/__init__.py`, replace lines 133-139:

```python
async def _check_origin(origin: str | None = Header(default=None)) -> None:
    """CSRF: allow localhost/127.0.0.1; 403 for any other origin; no origin = allow."""
    if origin is None:
        return  # no origin header → allow
    if "localhost" in origin or "127.0.0.1" in origin:
        return  # trusted local origin
    raise HTTPException(status_code=403, detail="CSRF: origin not allowed")
```

with:

```python
# Module-level allowed origins set (built once at import, refreshable)
_allowed_origins: set[str] = {"localhost", "127.0.0.1"}


def init_allowed_origins(extra: list[str] | None = None) -> None:
    """Rebuild the allowed origins set from server state.

    Called at startup after Tailscale detection and settings load.
    """
    global _allowed_origins  # noqa: PLW0603
    from amplifier_distro.server.origins import build_allowed_origins

    _allowed_origins = set(build_allowed_origins(extra=extra))


async def _check_origin(origin: str | None = Header(default=None)) -> None:
    """CSRF: allow origins in the dynamic allow-list; 403 for others; no origin = allow."""
    from amplifier_distro.server.origins import is_origin_allowed

    if not is_origin_allowed(origin, _allowed_origins):
        raise HTTPException(status_code=403, detail="CSRF: origin not allowed")
```

**Step 4: Run all CSRF tests to verify they pass**

```bash
cd distro-server && python -m pytest tests/test_voice_routes.py::TestCsrfProtection -v
```
Expected: All PASS (including the new Tailscale test)

**Step 5: Run full voice route tests to check for regressions**

```bash
cd distro-server && python -m pytest tests/test_voice_routes.py -v
```
Expected: All PASS

**Step 6: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: wire dynamic origin allow-list into voice CSRF check

Replace hardcoded localhost-only _check_origin with dynamic allow-list
using server/origins.py. Tailscale DNS, system hostname, and config
origins are now accepted. init_allowed_origins() called at startup.

Ref: #68"
```

---

## Phase 2: Browser UX

### Task 5: Add isSecureContext Guard to Voice App

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/voice/static/index.html` (around line 725-734)
- Test: `distro-server/tests/test_voice_static_secure_context.py`

**Context:** The voice app is a single HTML file using Preact + htm tagged templates (no JSX, no build step). The `connect()` function is defined at line 702. Before calling `navigator.mediaDevices.getUserMedia()` at line 728, we insert a secure context check. The existing error display pattern uses `.error-banner` CSS class (line 120 for CSS, line 2160 for usage). The test should verify the HTML string contains the guard — matching the existing test pattern in `test_voice_static_index.py`.

**Step 1: Write the failing test**

Create `distro-server/tests/test_voice_static_secure_context.py`:

```python
"""Tests for the isSecureContext guard in the voice app UI.

Verifies that the voice app's index.html contains the secure context
check that prevents cryptic errors on plain HTTP remote access.
"""

from __future__ import annotations

from pathlib import Path


class TestSecureContextGuard:
    """Voice app HTML contains isSecureContext check."""

    def _read_voice_html(self) -> str:
        html_path = (
            Path(__file__).parent.parent
            / "src"
            / "amplifier_distro"
            / "server"
            / "apps"
            / "voice"
            / "static"
            / "index.html"
        )
        return html_path.read_text()

    def test_html_contains_is_secure_context_check(self):
        html = self._read_voice_html()
        assert "isSecureContext" in html

    def test_html_contains_secure_connection_message(self):
        html = self._read_voice_html()
        assert "secure connection" in html.lower() or "secure context" in html.lower()

    def test_html_contains_tls_auto_suggestion(self):
        html = self._read_voice_html()
        assert "--tls auto" in html

    def test_guard_appears_before_get_user_media(self):
        """isSecureContext check must appear before getUserMedia call."""
        html = self._read_voice_html()
        secure_pos = html.index("isSecureContext")
        media_pos = html.index("getUserMedia")
        assert secure_pos < media_pos
```

**Step 2: Run test to verify it fails**

```bash
cd distro-server && python -m pytest tests/test_voice_static_secure_context.py -v
```
Expected: FAIL — `"isSecureContext" not in html`

**Step 3: Add the secure context guard to the voice HTML**

In `distro-server/src/amplifier_distro/server/apps/voice/static/index.html`, find the `connect()` function. Insert the guard inside `connect`, right after `updateState('connecting');` (line 703) and before `// Step 1: Fetch ephemeral token` (line 705). The new code goes between lines 703 and 705:

```javascript
      // Secure context guard: getUserMedia requires HTTPS or localhost
      if (!window.isSecureContext) {
        updateState('disconnected');
        setError(null); // clear generic error, show dedicated banner instead
        setSecureContextError(true);
        return;
      }
```

Next, add the `secureContextError` state variable. Find the `useState` declarations in the `VoiceApp` component. Look for the line with `const [error, setError] = useState(null);` (around line 2020-2030 area — search for `useState(null)` near other state declarations). Add after it:

```javascript
    const [secureContextError, setSecureContextError] = useState(false);
```

Next, add the UI banner. Find the existing error banner block (line 2160-2165):
```javascript
        ${error && html`
          <div class="error-banner">
            <span>⚠</span>
            <span>${error}</span>
          </div>
        `}
```

Add the secure context banner **immediately before** this error banner block:

```javascript
        ${secureContextError && html`
          <div class="error-banner" style="background:#1a1a2e;border-color:#2a2a4a;">
            <div>
              <strong>Voice requires a secure connection</strong>
              <div style="margin-top:8px;font-size:0.9em;opacity:0.85;">
                Your browser can't access the microphone over plain HTTP. To use voice remotely:
              </div>
              <ul style="margin:8px 0 0 0;padding-left:20px;font-size:0.9em;opacity:0.85;">
                <li>Run with <code>amp-distro serve --tls auto</code> to enable HTTPS</li>
                <li>Or access via <code>https://your-tailscale-hostname</code></li>
                <li>Voice works on <code>localhost</code> without any setup</li>
              </ul>
            </div>
          </div>
        `}
```

**Step 4: Run test to verify it passes**

```bash
cd distro-server && python -m pytest tests/test_voice_static_secure_context.py -v
```
Expected: All PASS

**Step 5: Run existing voice static tests to check for regressions**

```bash
cd distro-server && python -m pytest tests/test_voice_static_index.py tests/test_voice_routes.py -v
```
Expected: All PASS

**Step 6: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: add isSecureContext guard to voice app UI

Before calling getUserMedia, check window.isSecureContext. If false,
show an actionable banner explaining how to enable HTTPS for remote
voice access. Replaces the cryptic TypeError on plain HTTP.

Ref: #68"
```

---

## Phase 3: TLS Infrastructure

### Task 6: Add Tailscale Cert Provisioning

**Files:**
- Modify: `distro-server/src/amplifier_distro/tailscale.py`
- Modify: `distro-server/tests/test_tailscale.py`

**Context:** The existing `tailscale.py` has `get_dns_name()` and `start_serve()`. We need a new function `provision_cert()` that runs `tailscale cert <hostname>` to write cert/key files. This is separate from `tailscale serve` (which is a reverse proxy). The test pattern is already established in `test_tailscale.py` — class-based, mock `subprocess.run`, check args and return values.

**Step 1: Write the failing test**

Add to `distro-server/tests/test_tailscale.py`, after the `TestStopServe` class (after line 213):

```python
# ---------------------------------------------------------------------------
# provision_cert
# ---------------------------------------------------------------------------


class TestProvisionCert:
    """Tests for provision_cert()."""

    def test_returns_cert_paths_on_success(self, tmp_path):
        with (
            patch(
                "amplifier_distro.tailscale.get_dns_name",
                return_value="box.ts.net",
            ),
            patch(
                "amplifier_distro.tailscale.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                ),
            ) as mock_run,
        ):
            result = tailscale.provision_cert(cert_dir=tmp_path)
            assert result is not None
            certfile, keyfile = result
            assert certfile == tmp_path / "box.ts.net.crt"
            assert keyfile == tmp_path / "box.ts.net.key"
            # Verify correct command was called
            args = mock_run.call_args[0][0]
            assert args[0] == "tailscale"
            assert args[1] == "cert"
            assert "--cert-file" in args
            assert "--key-file" in args

    def test_returns_none_when_no_tailscale(self, tmp_path):
        with patch("amplifier_distro.tailscale.get_dns_name", return_value=None):
            assert tailscale.provision_cert(cert_dir=tmp_path) is None

    def test_returns_none_on_cert_failure(self, tmp_path):
        with (
            patch(
                "amplifier_distro.tailscale.get_dns_name",
                return_value="box.ts.net",
            ),
            patch(
                "amplifier_distro.tailscale.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="cert failed"
                ),
            ),
        ):
            assert tailscale.provision_cert(cert_dir=tmp_path) is None

    def test_returns_none_on_timeout(self, tmp_path):
        with (
            patch(
                "amplifier_distro.tailscale.get_dns_name",
                return_value="box.ts.net",
            ),
            patch(
                "amplifier_distro.tailscale.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="tailscale", timeout=30),
            ),
        ):
            assert tailscale.provision_cert(cert_dir=tmp_path) is None

    def test_creates_cert_dir_if_missing(self, tmp_path):
        cert_dir = tmp_path / "certs"
        with (
            patch(
                "amplifier_distro.tailscale.get_dns_name",
                return_value="box.ts.net",
            ),
            patch(
                "amplifier_distro.tailscale.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                ),
            ),
        ):
            tailscale.provision_cert(cert_dir=cert_dir)
            assert cert_dir.exists()
```

**Step 2: Run test to verify it fails**

```bash
cd distro-server && python -m pytest tests/test_tailscale.py::TestProvisionCert -v
```
Expected: FAIL — `AttributeError: module 'amplifier_distro.tailscale' has no attribute 'provision_cert'`

**Step 3: Implement provision_cert**

Add to `distro-server/src/amplifier_distro/tailscale.py`, after `stop_serve()` (after line 89):

```python
def provision_cert(cert_dir: Path) -> tuple[Path, Path] | None:
    """Provision a TLS certificate via ``tailscale cert``.

    Writes ``<hostname>.crt`` and ``<hostname>.key`` into *cert_dir*.
    Returns ``(certfile, keyfile)`` on success, or ``None`` on any failure.
    Failures are logged but never raise.
    """
    dns_name = get_dns_name()
    if dns_name is None:
        return None

    cert_dir.mkdir(parents=True, exist_ok=True)
    certfile = cert_dir / f"{dns_name}.crt"
    keyfile = cert_dir / f"{dns_name}.key"

    try:
        result = subprocess.run(
            [
                "tailscale", "cert",
                "--cert-file", str(certfile),
                "--key-file", str(keyfile),
                dns_name,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("tailscale cert failed: %s", result.stderr.strip())
            return None

        logger.info("Tailscale cert provisioned: %s", certfile)
        return certfile, keyfile

    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.debug("tailscale cert unavailable: %s", exc)
        return None
```

Also add the `Path` import at the top of the file. After line 7 (`import contextlib`), add:

```python
from pathlib import Path
```

**Step 4: Run test to verify it passes**

```bash
cd distro-server && python -m pytest tests/test_tailscale.py -v
```
Expected: All PASS (old and new tests)

**Step 5: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: add Tailscale cert provisioning via tailscale cert

New provision_cert() function runs 'tailscale cert <hostname>' to
write cert/key files for native TLS. Separate from tailscale serve
(reverse proxy mode). Returns (certfile, keyfile) or None on failure.

Ref: #68"
```

---

### Task 7: Add Self-Signed Cert Generation

**Files:**
- Create: `distro-server/src/amplifier_distro/server/tls.py`
- Test: `distro-server/tests/test_tls.py`

**Context:** When Tailscale is unavailable, we fall back to a self-signed cert generated using Python's `ssl` stdlib. Certs are written to `~/.amplifier-distro/certs/` (the `DISTRO_CERTS_DIR` convention from Task 1). Certs are generated once and reused.

**Step 1: Write the failing test**

Create `distro-server/tests/test_tls.py`:

```python
"""Tests for amplifier_distro.server.tls module.

Verifies self-signed cert generation and the cert resolution chain.
"""

from __future__ import annotations

import ssl
from pathlib import Path

from amplifier_distro.server.tls import generate_self_signed_cert


class TestGenerateSelfSignedCert:
    """Tests for generate_self_signed_cert()."""

    def test_creates_cert_and_key_files(self, tmp_path):
        certfile, keyfile = generate_self_signed_cert(cert_dir=tmp_path)
        assert certfile.exists()
        assert keyfile.exists()

    def test_cert_file_ends_with_pem(self, tmp_path):
        certfile, keyfile = generate_self_signed_cert(cert_dir=tmp_path)
        assert certfile.suffix == ".pem"
        assert keyfile.suffix == ".pem"

    def test_cert_is_loadable_by_ssl(self, tmp_path):
        certfile, keyfile = generate_self_signed_cert(cert_dir=tmp_path)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # Should not raise
        ctx.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))

    def test_reuses_existing_cert(self, tmp_path):
        certfile1, _ = generate_self_signed_cert(cert_dir=tmp_path)
        mtime1 = certfile1.stat().st_mtime

        certfile2, _ = generate_self_signed_cert(cert_dir=tmp_path)
        mtime2 = certfile2.stat().st_mtime

        assert mtime1 == mtime2, "Should reuse existing cert, not regenerate"

    def test_creates_cert_dir_if_missing(self, tmp_path):
        cert_dir = tmp_path / "nested" / "certs"
        certfile, _ = generate_self_signed_cert(cert_dir=cert_dir)
        assert cert_dir.exists()
        assert certfile.exists()
```

**Step 2: Run test to verify it fails**

```bash
cd distro-server && python -m pytest tests/test_tls.py::TestGenerateSelfSignedCert -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'amplifier_distro.server.tls'`

**Step 3: Implement self-signed cert generation**

Create `distro-server/src/amplifier_distro/server/tls.py`:

```python
"""TLS certificate management for amplifier-distro.

Provides self-signed cert generation and cert resolution chain
(manual → Tailscale → self-signed fallback).
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import os
import ssl
from pathlib import Path

logger = logging.getLogger(__name__)

# Self-signed cert filenames
_SELF_SIGNED_CERT = "self-signed.pem"
_SELF_SIGNED_KEY = "self-signed-key.pem"


def generate_self_signed_cert(cert_dir: Path) -> tuple[Path, Path]:
    """Generate a self-signed TLS certificate if one doesn't already exist.

    Args:
        cert_dir: Directory to write cert/key files into.

    Returns:
        Tuple of (certfile_path, keyfile_path).
    """
    cert_dir.mkdir(parents=True, exist_ok=True)
    certfile = cert_dir / _SELF_SIGNED_CERT
    keyfile = cert_dir / _SELF_SIGNED_KEY

    # Reuse existing cert
    if certfile.exists() and keyfile.exists():
        logger.info("Reusing existing self-signed cert: %s", certfile)
        return certfile, keyfile

    logger.info("Generating self-signed TLS certificate in %s", cert_dir)

    # Use the ssl stdlib to generate a self-signed cert via a subprocess
    # call to openssl, or use the cryptography approach if available.
    # Simplest portable approach: use ssl module's built-in.
    import subprocess
    import socket

    hostname = socket.gethostname()

    # Generate using openssl CLI (universally available on Linux/macOS)
    try:
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(keyfile),
                "-out", str(certfile),
                "-days", "3650",
                "-nodes",
                "-subj", f"/CN={hostname}/O=Amplifier Distro Self-Signed",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Fallback: use Python's ssl module to create a minimal cert
        _generate_cert_python(certfile, keyfile, hostname)

    # Restrict permissions on key file
    try:
        os.chmod(keyfile, 0o600)
    except OSError:
        pass

    logger.info("Self-signed cert generated: %s", certfile)
    return certfile, keyfile


def _generate_cert_python(certfile: Path, keyfile: Path, hostname: str) -> None:
    """Pure-Python self-signed cert generation using the ssl stdlib.

    Uses the private _ssl._test_decode_cert approach as a fallback.
    In practice, openssl CLI is preferred (see generate_self_signed_cert).
    """
    try:
        # Try using the cryptography library if available
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, hostname),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Amplifier Distro Self-Signed"),
        ])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.UTC))
            .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=3650))
            .sign(key, hashes.SHA256())
        )
        keyfile.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    except ImportError:
        msg = (
            "Cannot generate self-signed cert: neither openssl CLI nor "
            "the 'cryptography' Python package is available. "
            "Install cryptography: pip install cryptography"
        )
        raise RuntimeError(msg) from None
```

**Step 4: Run test to verify it passes**

```bash
cd distro-server && python -m pytest tests/test_tls.py::TestGenerateSelfSignedCert -v
```
Expected: All PASS

**Step 5: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: add self-signed TLS certificate generation

New server/tls.py generates self-signed certs using openssl CLI with
cryptography library fallback. Certs are generated once and reused.
Key files get 0o600 permissions.

Ref: #68"
```

---

### Task 8: Build Cert Resolution Chain

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/tls.py`
- Modify: `distro-server/tests/test_tls.py`

**Context:** The cert resolution chain tries: manual paths → Tailscale cert → self-signed fallback. This is the function that `_run_foreground` will call to get cert/key paths for uvicorn.

**Step 1: Write the failing test**

Add to `distro-server/tests/test_tls.py`:

```python
from unittest.mock import patch

from amplifier_distro.server.tls import resolve_cert


class TestResolveCert:
    """Tests for the cert resolution chain."""

    def test_manual_mode_returns_provided_paths(self, tmp_path):
        certfile = tmp_path / "cert.pem"
        keyfile = tmp_path / "key.pem"
        certfile.touch()
        keyfile.touch()
        result = resolve_cert(
            mode="manual", certfile=str(certfile), keyfile=str(keyfile), cert_dir=tmp_path
        )
        assert result is not None
        assert result[0] == certfile
        assert result[1] == keyfile

    def test_manual_mode_returns_none_if_cert_missing(self, tmp_path):
        result = resolve_cert(
            mode="manual",
            certfile="/nonexistent/cert.pem",
            keyfile="/nonexistent/key.pem",
            cert_dir=tmp_path,
        )
        assert result is None

    def test_auto_mode_tries_tailscale_first(self, tmp_path):
        ts_cert = tmp_path / "box.ts.net.crt"
        ts_key = tmp_path / "box.ts.net.key"
        with patch(
            "amplifier_distro.server.tls.provision_cert",
            return_value=(ts_cert, ts_key),
        ):
            result = resolve_cert(mode="auto", cert_dir=tmp_path)
            assert result == (ts_cert, ts_key)

    def test_auto_mode_falls_back_to_self_signed(self, tmp_path):
        with patch(
            "amplifier_distro.server.tls.provision_cert",
            return_value=None,
        ):
            result = resolve_cert(mode="auto", cert_dir=tmp_path)
            assert result is not None
            certfile, keyfile = result
            assert certfile.exists()
            assert keyfile.exists()

    def test_off_mode_returns_none(self, tmp_path):
        result = resolve_cert(mode="off", cert_dir=tmp_path)
        assert result is None
```

**Step 2: Run test to verify it fails**

```bash
cd distro-server && python -m pytest tests/test_tls.py::TestResolveCert -v
```
Expected: FAIL — `ImportError: cannot import name 'resolve_cert'`

**Step 3: Implement resolve_cert**

Add to `distro-server/src/amplifier_distro/server/tls.py`, after `_generate_cert_python`:

```python
def resolve_cert(
    mode: str = "off",
    certfile: str = "",
    keyfile: str = "",
    cert_dir: Path | None = None,
) -> tuple[Path, Path] | None:
    """Resolve TLS certificate based on mode.

    Resolution chain for mode="auto":
      1. Tailscale cert (``tailscale cert <hostname>``)
      2. Self-signed cert fallback

    Args:
        mode: "off" | "auto" | "manual"
        certfile: Path to cert file (manual mode).
        keyfile: Path to key file (manual mode).
        cert_dir: Directory for auto-provisioned certs.

    Returns:
        (certfile, keyfile) paths, or None if TLS should be disabled.
    """
    if mode == "off":
        return None

    if mode == "manual":
        cert_path = Path(certfile)
        key_path = Path(keyfile)
        if cert_path.exists() and key_path.exists():
            return cert_path, key_path
        logger.error(
            "TLS manual mode: cert or key file not found: %s, %s",
            certfile, keyfile,
        )
        return None

    # mode == "auto"
    if cert_dir is None:
        from amplifier_distro import conventions
        cert_dir = Path(conventions.DISTRO_CERTS_DIR).expanduser()

    # Try Tailscale first
    from amplifier_distro.tailscale import provision_cert

    ts_result = provision_cert(cert_dir=cert_dir)
    if ts_result is not None:
        logger.info("Using Tailscale cert for TLS")
        return ts_result

    # Fall back to self-signed
    logger.warning(
        "Tailscale not available — generating self-signed certificate. "
        "Browsers will show a security warning on first visit."
    )
    return generate_self_signed_cert(cert_dir=cert_dir)
```

**Step 4: Run test to verify it passes**

```bash
cd distro-server && python -m pytest tests/test_tls.py -v
```
Expected: All PASS

**Step 5: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: add cert resolution chain (manual -> Tailscale -> self-signed)

resolve_cert() tries manual paths, then Tailscale cert provisioning,
then self-signed fallback. Returns (certfile, keyfile) or None.

Ref: #68"
```

---

### Task 9: Wire TLS into Uvicorn Startup

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/cli.py` (lines 344-501, the `_run_foreground` function)
- Test: `distro-server/tests/test_tls_startup.py`

**Context:** `_run_foreground()` in `server/cli.py` calls `uvicorn.run()` at lines 494-501. We need to pass `ssl_certfile` and `ssl_keyfile` when TLS is enabled. The function currently accepts `host, port, apps_dir, reload, dev, stub` parameters. We add `tls_mode, ssl_certfile, ssl_keyfile` parameters.

**Step 1: Write the failing test**

Create `distro-server/tests/test_tls_startup.py`:

```python
"""Tests for TLS wiring into uvicorn startup.

Verifies that resolve_cert is called and its result is passed to uvicorn.run.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


class TestTlsStartupWiring:
    """Verify _run_foreground passes TLS args to uvicorn."""

    def test_uvicorn_receives_ssl_args_when_tls_auto(self, tmp_path):
        """When tls_mode='auto', uvicorn.run gets ssl_certfile and ssl_keyfile."""
        fake_cert = tmp_path / "cert.pem"
        fake_key = tmp_path / "key.pem"
        fake_cert.touch()
        fake_key.touch()

        with (
            patch("amplifier_distro.server.cli.resolve_cert", return_value=(fake_cert, fake_key)),
            patch("amplifier_distro.server.cli.uvicorn") as mock_uvicorn,
            patch("amplifier_distro.server.cli.create_server") as mock_create,
            patch("amplifier_distro.server.cli.init_services"),
            patch("amplifier_distro.server.cli.load_env_file", return_value=[]),
            patch("amplifier_distro.server.cli.export_keys", return_value=[]),
            patch("amplifier_distro.server.cli.setup_logging"),
            patch("amplifier_distro.server.cli.create_session_dir", return_value=("sid", tmp_path)),
            patch("amplifier_distro.server.cli.setup_session_log", return_value=tmp_path / "log"),
            patch("amplifier_distro.server.cli.log_startup_info"),
            patch("amplifier_distro.server.cli._setup_tailscale", return_value=None),
        ):
            mock_server = MagicMock()
            mock_server.app = MagicMock()
            mock_server.discover_apps.return_value = []
            mock_create.return_value = mock_server

            from amplifier_distro.server.cli import _run_foreground

            _run_foreground("0.0.0.0", 8400, None, False, False, stub=False, tls_mode="auto")

            call_kwargs = mock_uvicorn.run.call_args[1]
            assert call_kwargs.get("ssl_certfile") == str(fake_cert)
            assert call_kwargs.get("ssl_keyfile") == str(fake_key)

    def test_uvicorn_has_no_ssl_args_when_tls_off(self, tmp_path):
        """When tls_mode='off', uvicorn.run has no ssl_certfile."""
        with (
            patch("amplifier_distro.server.cli.uvicorn") as mock_uvicorn,
            patch("amplifier_distro.server.cli.create_server") as mock_create,
            patch("amplifier_distro.server.cli.init_services"),
            patch("amplifier_distro.server.cli.load_env_file", return_value=[]),
            patch("amplifier_distro.server.cli.export_keys", return_value=[]),
            patch("amplifier_distro.server.cli.setup_logging"),
            patch("amplifier_distro.server.cli.create_session_dir", return_value=("sid", tmp_path)),
            patch("amplifier_distro.server.cli.setup_session_log", return_value=tmp_path / "log"),
            patch("amplifier_distro.server.cli.log_startup_info"),
            patch("amplifier_distro.server.cli._setup_tailscale", return_value=None),
        ):
            mock_server = MagicMock()
            mock_server.app = MagicMock()
            mock_server.discover_apps.return_value = []
            mock_create.return_value = mock_server

            from amplifier_distro.server.cli import _run_foreground

            _run_foreground("0.0.0.0", 8400, None, False, False, stub=False, tls_mode="off")

            call_kwargs = mock_uvicorn.run.call_args[1]
            assert "ssl_certfile" not in call_kwargs
```

**Step 2: Run test to verify it fails**

```bash
cd distro-server && python -m pytest tests/test_tls_startup.py -v
```
Expected: FAIL — `_run_foreground() got an unexpected keyword argument 'tls_mode'`

**Step 3: Add TLS parameters to _run_foreground**

In `distro-server/src/amplifier_distro/server/cli.py`, modify `_run_foreground` (line 344) to accept TLS parameters. Change the function signature from:

```python
def _run_foreground(
    host: str,
    port: int,
    apps_dir: str | None,
    reload: bool,
    dev: bool,
    *,
    stub: bool = False,
) -> None:
```

to:

```python
def _run_foreground(
    host: str,
    port: int,
    apps_dir: str | None,
    reload: bool,
    dev: bool,
    *,
    stub: bool = False,
    tls_mode: str = "off",
    ssl_certfile: str = "",
    ssl_keyfile: str = "",
) -> None:
```

Add the TLS resolution logic after the Tailscale setup block (after line 459, after `ts_url = _setup_tailscale(port)`). Add:

```python
    # TLS resolution
    from amplifier_distro.server.tls import resolve_cert

    tls_paths = resolve_cert(
        mode=tls_mode,
        certfile=ssl_certfile,
        keyfile=ssl_keyfile,
    )

    ssl_kwargs: dict[str, str] = {}
    if tls_paths is not None:
        ssl_kwargs["ssl_certfile"] = str(tls_paths[0])
        ssl_kwargs["ssl_keyfile"] = str(tls_paths[1])
        scheme = "https"
    else:
        scheme = "http"
```

Update the startup echo lines to use the `scheme` variable. Change:
```python
    click.echo(f"  Local: http://{host}:{port}")
```
to:
```python
    click.echo(f"  Local: {scheme}://{host}:{port}")
```

Update the two `uvicorn.run()` calls to include `**ssl_kwargs`. Change the non-reload call (line 494-501) from:

```python
        uvicorn.run(
            server.app,
            host=host,
            port=port,
            log_level="info",
            ws_ping_interval=20,
            ws_ping_timeout=20,
        )
```

to:

```python
        uvicorn.run(
            server.app,
            host=host,
            port=port,
            log_level="info",
            ws_ping_interval=20,
            ws_ping_timeout=20,
            **ssl_kwargs,
        )
```

Do the same for the reload-mode `uvicorn.run()` call (around line 483-492).

**Step 4: Run test to verify it passes**

```bash
cd distro-server && python -m pytest tests/test_tls_startup.py -v
```
Expected: All PASS

**Step 5: Run existing server CLI tests**

```bash
cd distro-server && python -m pytest tests/test_cli.py -v
```
Expected: All PASS (no regressions — default tls_mode="off" keeps current behavior)

**Step 6: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: wire TLS cert resolution into uvicorn startup

_run_foreground() now accepts tls_mode, ssl_certfile, ssl_keyfile.
Calls resolve_cert() and passes ssl args to uvicorn.run().
Default tls_mode='off' preserves current HTTP-only behavior.

Ref: #68"
```

---

### Task 10: Add --tls CLI Flags

**Files:**
- Modify: `distro-server/src/amplifier_distro/cli.py` (lines 50-81, the `serve_cmd` function)
- Modify: `distro-server/src/amplifier_distro/server/cli.py` (lines 25-73, the `serve` group)
- Test: `distro-server/tests/test_cli_tls_flags.py`

**Context:** There are TWO CLI entry points: `cli.py:serve_cmd` (the `amp-distro serve` shortcut) and `server/cli.py:serve` (the full serve group with start/stop/restart). Both need the new flags. `cli.py:serve_cmd` delegates to `server/cli.py:_run_foreground`. The flag pattern is `@click.option(...)` — see existing flags for the pattern.

**Step 1: Write the failing test**

Create `distro-server/tests/test_cli_tls_flags.py`:

```python
"""Tests for --tls CLI flags on the serve command.

Verifies the new TLS flags exist and are passed through to _run_foreground.
"""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from amplifier_distro.cli import main


class TestTlsCliFlags:
    """The 'serve' command accepts --tls and --ssl-certfile/--ssl-keyfile flags."""

    def test_tls_auto_flag_accepted(self):
        runner = CliRunner()
        with patch("amplifier_distro.cli._run_foreground") as mock_run:
            result = runner.invoke(main, ["serve", "--tls", "auto"])
            # Should not fail due to unknown option
            assert result.exit_code == 0 or "no such option" not in (result.output or "").lower()
            if result.exit_code == 0:
                call_kwargs = mock_run.call_args
                # tls_mode should be passed as keyword arg
                assert call_kwargs[1].get("tls_mode") == "auto" or "auto" in str(call_kwargs)

    def test_tls_off_is_default(self):
        runner = CliRunner()
        with patch("amplifier_distro.cli._run_foreground") as mock_run:
            result = runner.invoke(main, ["serve"])
            if result.exit_code == 0:
                call_kwargs = mock_run.call_args
                assert call_kwargs[1].get("tls_mode") == "off" or mock_run.called

    def test_ssl_certfile_implies_manual(self):
        runner = CliRunner()
        with patch("amplifier_distro.cli._run_foreground") as mock_run:
            result = runner.invoke(
                main, ["serve", "--ssl-certfile", "/tmp/cert.pem", "--ssl-keyfile", "/tmp/key.pem"]
            )
            if result.exit_code == 0:
                call_kwargs = mock_run.call_args
                assert call_kwargs[1].get("tls_mode") == "manual"

    def test_no_auth_flag_accepted(self):
        runner = CliRunner()
        with patch("amplifier_distro.cli._run_foreground") as mock_run:
            result = runner.invoke(main, ["serve", "--no-auth"])
            assert "no such option" not in (result.output or "").lower()
```

**Step 2: Run test to verify it fails**

```bash
cd distro-server && python -m pytest tests/test_cli_tls_flags.py -v
```
Expected: FAIL — `no such option: --tls`

**Step 3: Add TLS flags to cli.py serve_cmd**

In `distro-server/src/amplifier_distro/cli.py`, add these `@click.option` decorators to `serve_cmd` (after the `--stub` option on line 66, before the function `def serve_cmd`):

```python
@click.option(
    "--tls",
    "tls_mode",
    default="off",
    type=click.Choice(["auto", "off", "manual"]),
    help="TLS mode: auto (Tailscale/self-signed), off (plain HTTP), manual (provide certs)",
)
@click.option(
    "--ssl-certfile",
    default="",
    help="Path to TLS certificate file (implies --tls manual)",
)
@click.option(
    "--ssl-keyfile",
    default="",
    help="Path to TLS private key file (implies --tls manual)",
)
@click.option(
    "--no-auth",
    is_flag=True,
    help="Disable PAM authentication even when TLS is active",
)
```

Update the `serve_cmd` function signature to accept the new parameters:

```python
def serve_cmd(
    host: str,
    port: int,
    apps_dir: str | None,
    reload: bool,
    dev: bool,
    stub: bool,
    tls_mode: str,
    ssl_certfile: str,
    ssl_keyfile: str,
    no_auth: bool,
) -> None:
```

Update the body to pass TLS params through. The `--ssl-certfile` flag implies manual mode. Replace the existing function body:

```python
    """Start the experience server."""
    from .server.cli import _run_foreground

    if stub:
        dev = True
    # --ssl-certfile implies manual TLS mode
    if ssl_certfile and tls_mode == "off":
        tls_mode = "manual"
    _run_foreground(
        host, port, apps_dir, reload, dev,
        stub=stub,
        tls_mode=tls_mode,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
    )
```

**Step 4: Run test to verify it passes**

```bash
cd distro-server && python -m pytest tests/test_cli_tls_flags.py -v
```
Expected: All PASS

**Step 5: Run existing CLI tests**

```bash
cd distro-server && python -m pytest tests/test_cli.py -v
```
Expected: All PASS (default tls_mode="off" is backward-compatible)

**Step 6: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: add --tls, --ssl-certfile, --ssl-keyfile, --no-auth CLI flags

New CLI flags on 'amp-distro serve':
  --tls auto|off|manual  (default: off)
  --ssl-certfile / --ssl-keyfile  (implies --tls manual)
  --no-auth  (disable PAM when TLS is active)

Zero breaking changes: plain 'amp-distro serve' stays HTTP.

Ref: #68"
```

---

## Phase 4: PAM Authentication

### Task 11: Add PAM Auth Module

**Files:**
- Create: `distro-server/src/amplifier_distro/server/auth.py`
- Test: `distro-server/tests/test_auth.py`

**Context:** PAM authentication uses the `python-pam` library. We wrap it to: (1) call `pam.authenticate()`, (2) log `pam.reason` on failure (server-side only), (3) never expose PAM internals to the client. This follows the pattern from https://github.com/robotdad/filebrowser.

**Step 1: Write the failing test**

Create `distro-server/tests/test_auth.py`:

```python
"""Tests for amplifier_distro.server.auth module.

Verifies PAM authentication wrapper and session token management.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from amplifier_distro.server.auth import authenticate_pam, is_auth_applicable


class TestAuthenticatePam:
    """Tests for PAM authentication wrapper."""

    def test_returns_true_on_success(self):
        mock_pam = MagicMock()
        mock_pam.authenticate.return_value = True
        with patch("amplifier_distro.server.auth.pam.pam", return_value=mock_pam):
            assert authenticate_pam("testuser", "testpass") is True

    def test_returns_false_on_failure(self):
        mock_pam = MagicMock()
        mock_pam.authenticate.return_value = False
        mock_pam.reason = "Authentication failure"
        with patch("amplifier_distro.server.auth.pam.pam", return_value=mock_pam):
            assert authenticate_pam("testuser", "wrongpass") is False

    def test_logs_pam_reason_on_failure(self):
        mock_pam = MagicMock()
        mock_pam.authenticate.return_value = False
        mock_pam.reason = "user not in shadow group"
        with (
            patch("amplifier_distro.server.auth.pam.pam", return_value=mock_pam),
            patch("amplifier_distro.server.auth.logger") as mock_logger,
        ):
            authenticate_pam("testuser", "wrongpass")
            mock_logger.warning.assert_called_once()
            assert "user not in shadow group" in str(mock_logger.warning.call_args)

    def test_returns_false_when_pam_unavailable(self):
        with patch("amplifier_distro.server.auth.pam", None):
            assert authenticate_pam("testuser", "testpass") is False


class TestIsAuthApplicable:
    """Tests for auth applicability checks."""

    def test_not_applicable_when_tls_off(self):
        assert is_auth_applicable(tls_active=False, platform="Linux") is False

    def test_not_applicable_on_macos(self):
        assert is_auth_applicable(tls_active=True, platform="Darwin") is False

    def test_applicable_on_linux_with_tls(self):
        assert is_auth_applicable(tls_active=True, platform="Linux") is True

    def test_not_applicable_when_disabled(self):
        assert is_auth_applicable(tls_active=True, platform="Linux", auth_enabled=False) is False
```

**Step 2: Run test to verify it fails**

```bash
cd distro-server && python -m pytest tests/test_auth.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'amplifier_distro.server.auth'`

**Step 3: Implement the auth module**

Create `distro-server/src/amplifier_distro/server/auth.py`:

```python
"""PAM authentication and session management for amplifier-distro.

Provides:
- PAM authentication wrapper (Linux/WSL only)
- Session token creation/verification (itsdangerous)
- Auth applicability checks
- FastAPI middleware and dependencies for auth enforcement
"""

from __future__ import annotations

import logging
import os
import platform
from pathlib import Path

logger = logging.getLogger(__name__)

# Conditional PAM import — None on macOS/Windows
try:
    import pam
except ImportError:
    pam = None  # type: ignore[assignment]


def authenticate_pam(username: str, password: str) -> bool:
    """Authenticate a user against PAM.

    Args:
        username: Linux username.
        password: User password.

    Returns:
        True if authentication succeeded, False otherwise.
        PAM failure reason is logged server-side but never exposed to client.
    """
    if pam is None:
        logger.warning("PAM authentication unavailable (python-pam not installed)")
        return False

    p = pam.pam()
    if p.authenticate(username, password):
        return True

    # Log the PAM reason server-side for diagnostics
    reason = getattr(p, "reason", "unknown")
    logger.warning(
        "PAM authentication failed for user '%s': %s",
        username,
        reason,
    )
    return False


def is_auth_applicable(
    tls_active: bool,
    platform: str | None = None,
    auth_enabled: bool = True,
) -> bool:
    """Check whether PAM auth should be enforced.

    Auth is only active when ALL of:
    - TLS is active (no auth over plain HTTP)
    - Platform is Linux (or WSL)
    - auth_enabled is True in settings

    Args:
        tls_active: Whether TLS is enabled for this server run.
        platform: Platform string (defaults to platform.system()).
        auth_enabled: Whether auth is enabled in settings.

    Returns:
        True if auth should be enforced.
    """
    if platform is None:
        import platform as _platform
        platform = _platform.system()

    if not auth_enabled:
        return False
    if not tls_active:
        return False
    if platform not in ("Linux",):
        return False
    return True
```

**Step 4: Run test to verify it passes**

```bash
cd distro-server && python -m pytest tests/test_auth.py -v
```
Expected: All PASS

**Step 5: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: add PAM authentication module

New server/auth.py wraps python-pam with:
- authenticate_pam(): validates user/pass, logs pam.reason on failure
- is_auth_applicable(): checks TLS + Linux + settings before enabling
- Conditional pam import (None on macOS/Windows)

Ref: #68"
```

---

### Task 12: Add Session Token Management

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/auth.py`
- Modify: `distro-server/tests/test_auth.py`

**Context:** Session tokens use `itsdangerous.TimestampSigner` for signed cookies. The secret key is auto-generated on first run and stored at `~/.amplifier-distro/`. Cookies are HttpOnly, Secure, SameSite=Strict.

**Step 1: Write the failing test**

Add to `distro-server/tests/test_auth.py`:

```python
import time

from amplifier_distro.server.auth import create_session_token, verify_session_token


class TestSessionTokens:
    """Tests for session token creation and verification."""

    def test_create_and_verify_round_trip(self):
        token = create_session_token("testuser", secret="test-secret")
        result = verify_session_token(token, secret="test-secret", max_age=3600)
        assert result == "testuser"

    def test_invalid_token_returns_none(self):
        result = verify_session_token("garbage-token", secret="test-secret", max_age=3600)
        assert result is None

    def test_wrong_secret_returns_none(self):
        token = create_session_token("testuser", secret="secret-a")
        result = verify_session_token(token, secret="secret-b", max_age=3600)
        assert result is None

    def test_expired_token_returns_none(self):
        token = create_session_token("testuser", secret="test-secret")
        # max_age=0 means expired immediately
        result = verify_session_token(token, secret="test-secret", max_age=0)
        assert result is None


class TestGetOrCreateSecret:
    """Tests for secret key auto-generation."""

    def test_creates_secret_file(self, tmp_path):
        from amplifier_distro.server.auth import get_or_create_secret

        secret = get_or_create_secret(secret_dir=tmp_path)
        assert isinstance(secret, str)
        assert len(secret) > 16

    def test_reuses_existing_secret(self, tmp_path):
        from amplifier_distro.server.auth import get_or_create_secret

        secret1 = get_or_create_secret(secret_dir=tmp_path)
        secret2 = get_or_create_secret(secret_dir=tmp_path)
        assert secret1 == secret2
```

**Step 2: Run test to verify it fails**

```bash
cd distro-server && python -m pytest tests/test_auth.py::TestSessionTokens -v
```
Expected: FAIL — `ImportError: cannot import name 'create_session_token'`

**Step 3: Implement session token management**

Add to `distro-server/src/amplifier_distro/server/auth.py`, after the `is_auth_applicable` function:

```python
# ---------------------------------------------------------------------------
# Session Tokens (itsdangerous)
# ---------------------------------------------------------------------------

_SECRET_FILENAME = "session-secret.key"


def get_or_create_secret(secret_dir: Path | None = None) -> str:
    """Get or auto-generate the session signing secret.

    The secret is stored in a file inside secret_dir. Generated once
    on first run and reused thereafter.

    Args:
        secret_dir: Directory to store the secret file.
            Defaults to DISTRO_HOME.

    Returns:
        The secret key string.
    """
    if secret_dir is None:
        from amplifier_distro import conventions
        secret_dir = Path(conventions.DISTRO_HOME).expanduser()

    secret_file = secret_dir / _SECRET_FILENAME
    if secret_file.exists():
        return secret_file.read_text().strip()

    import secrets
    secret = secrets.token_hex(32)
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(secret)
    try:
        os.chmod(secret_file, 0o600)
    except OSError:
        pass
    logger.info("Generated new session secret: %s", secret_file)
    return secret


def create_session_token(username: str, secret: str) -> str:
    """Create a signed session token for a user.

    Args:
        username: The authenticated username.
        secret: The signing secret key.

    Returns:
        A signed, timestamped token string.
    """
    from itsdangerous import TimestampSigner

    signer = TimestampSigner(secret)
    return signer.sign(username).decode("utf-8")


def verify_session_token(
    token: str, secret: str, max_age: int = 2592000
) -> str | None:
    """Verify a session token and return the username.

    Args:
        token: The signed token string.
        secret: The signing secret key.
        max_age: Maximum token age in seconds (default 30 days).

    Returns:
        The username if valid, None if invalid or expired.
    """
    from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

    signer = TimestampSigner(secret)
    try:
        value = signer.unsign(token, max_age=max_age)
        return value.decode("utf-8")
    except (BadSignature, SignatureExpired):
        return None
```

**Step 4: Run test to verify it passes**

```bash
cd distro-server && python -m pytest tests/test_auth.py -v
```
Expected: All PASS

**Step 5: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: add session token management with itsdangerous

- create_session_token() / verify_session_token() using TimestampSigner
- get_or_create_secret() auto-generates signing key on first run
- Secret stored at ~/.amplifier-distro/session-secret.key (mode 0o600)
- 30-day default session timeout

Ref: #68"
```

---

### Task 13: Add Auth Routes

**Files:**
- Create: `distro-server/src/amplifier_distro/server/auth_routes.py`
- Modify: `distro-server/tests/test_auth.py`

**Context:** Auth routes are a FastAPI APIRouter with `/login` (POST), `/logout` (POST), `/auth/me` (GET). They use the auth module from Task 11-12. The login page is served as static HTML (Task 14). Test pattern: httpx.AsyncClient with ASGITransport, matching `conftest.py:async_webchat_client`.

**Step 1: Write the failing test**

Add to `distro-server/tests/test_auth.py`:

```python
import httpx
from fastapi import FastAPI

from amplifier_distro.server.auth_routes import create_auth_router


def _make_auth_app(secret: str = "test-secret", session_timeout: int = 3600) -> FastAPI:
    """Build a minimal FastAPI app with auth routes."""
    app = FastAPI()
    router = create_auth_router(secret=secret, session_timeout=session_timeout)
    app.include_router(router)
    return app


class TestLoginRoute:
    """Tests for POST /login."""

    async def test_successful_login_sets_cookie(self):
        app = _make_auth_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("amplifier_distro.server.auth_routes.authenticate_pam", return_value=True):
                resp = await client.post(
                    "/login",
                    data={"username": "testuser", "password": "testpass"},
                )
                assert resp.status_code == 303  # redirect after login
                assert "session" in resp.headers.get("set-cookie", "").lower()

    async def test_failed_login_returns_401(self):
        app = _make_auth_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("amplifier_distro.server.auth_routes.authenticate_pam", return_value=False):
                resp = await client.post(
                    "/login",
                    data={"username": "testuser", "password": "wrong"},
                )
                assert resp.status_code == 401


class TestAuthMeRoute:
    """Tests for GET /auth/me."""

    async def test_returns_username_with_valid_cookie(self):
        from amplifier_distro.server.auth import create_session_token

        app = _make_auth_app()
        token = create_session_token("testuser", secret="test-secret")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/auth/me",
                cookies={"amplifier_session": token},
            )
            assert resp.status_code == 200
            assert resp.json()["username"] == "testuser"

    async def test_returns_401_without_cookie(self):
        app = _make_auth_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/auth/me")
            assert resp.status_code == 401


class TestLogoutRoute:
    """Tests for POST /logout."""

    async def test_logout_clears_cookie(self):
        app = _make_auth_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/logout")
            cookie_header = resp.headers.get("set-cookie", "")
            # Cookie should be cleared (max-age=0 or expired)
            assert "amplifier_session" in cookie_header
```

**Step 2: Run test to verify it fails**

```bash
cd distro-server && python -m pytest tests/test_auth.py::TestLoginRoute -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'amplifier_distro.server.auth_routes'`

**Step 3: Implement auth routes**

Create `distro-server/src/amplifier_distro/server/auth_routes.py`:

```python
"""Authentication routes for amplifier-distro.

Provides login, logout, and session-check endpoints.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Cookie, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from amplifier_distro.server.auth import (
    authenticate_pam,
    create_session_token,
    verify_session_token,
)

logger = logging.getLogger(__name__)

_COOKIE_NAME = "amplifier_session"


def create_auth_router(secret: str, session_timeout: int = 2592000) -> APIRouter:
    """Create a FastAPI router with auth endpoints.

    Args:
        secret: The session signing secret.
        session_timeout: Session cookie max-age in seconds.

    Returns:
        APIRouter with /login, /logout, /auth/me routes.
    """
    router = APIRouter(tags=["auth"])

    @router.get("/login", response_class=HTMLResponse)
    async def login_page() -> HTMLResponse:
        """Serve the login page."""
        login_html = Path(__file__).parent / "static" / "login.html"
        if login_html.exists():
            return HTMLResponse(content=login_html.read_text())
        return HTMLResponse(
            content="<html><body><h1>Login</h1>"
            '<form method="post" action="/login">'
            '<input name="username" placeholder="Username">'
            '<input name="password" type="password" placeholder="Password">'
            '<button type="submit">Login</button>'
            "</form></body></html>"
        )

    @router.post("/login")
    async def login(
        response: Response,
        username: str = Form(...),
        password: str = Form(...),
    ) -> Response:
        """Authenticate via PAM and set session cookie."""
        if not authenticate_pam(username, password):
            return JSONResponse(
                status_code=401,
                content={"error": "Authentication failed"},
            )

        token = create_session_token(username, secret=secret)
        redirect = RedirectResponse(url="/", status_code=303)
        redirect.set_cookie(
            key=_COOKIE_NAME,
            value=token,
            max_age=session_timeout,
            httponly=True,
            secure=True,
            samesite="strict",
        )
        return redirect

    @router.post("/logout")
    async def logout() -> Response:
        """Clear the session cookie."""
        response = RedirectResponse(url="/login", status_code=303)
        response.delete_cookie(key=_COOKIE_NAME)
        return response

    @router.get("/auth/me")
    async def auth_me(
        amplifier_session: str | None = Cookie(default=None),
    ) -> JSONResponse:
        """Return the authenticated username, or 401."""
        if amplifier_session is None:
            return JSONResponse(status_code=401, content={"error": "Not authenticated"})

        username = verify_session_token(
            amplifier_session, secret=secret, max_age=session_timeout
        )
        if username is None:
            return JSONResponse(status_code=401, content={"error": "Session expired"})

        return JSONResponse(content={"username": username})

    return router
```

**Step 4: Run test to verify it passes**

```bash
cd distro-server && python -m pytest tests/test_auth.py -v
```
Expected: All PASS

**Step 5: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: add auth routes (login, logout, auth/me)

New server/auth_routes.py with:
- POST /login: PAM auth -> session cookie (HttpOnly, Secure, SameSite=Strict)
- POST /logout: clear cookie
- GET /auth/me: return authenticated username or 401
- GET /login: serve login page HTML

Ref: #68"
```

---

### Task 14: Add Login Page HTML

**Files:**
- Create: `distro-server/src/amplifier_distro/server/static/login.html`

**Context:** Login page is a static HTML file served at `/login`. Should match the visual style of the existing server UI (dark theme with Amplifier branding). The existing `static/` directory has `amplifier-theme.css`, `styles.css`, `favicon.svg`, and `index.html`.

**Step 1: Create the login page**

Create `distro-server/src/amplifier_distro/server/static/login.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Login - Amplifier Distro</title>
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/static/amplifier-theme.css">
  <style>
    body {
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 100vh;
      margin: 0;
      background: var(--bg-primary, #0a0a0a);
      color: var(--text-primary, #e0e0e0);
      font-family: system-ui, -apple-system, sans-serif;
    }
    .login-card {
      background: var(--bg-secondary, #1a1a1a);
      border: 1px solid var(--border-primary, #333);
      border-radius: 12px;
      padding: 40px;
      width: 100%;
      max-width: 380px;
      box-shadow: 0 4px 24px rgba(0,0,0,0.3);
    }
    .login-card h1 {
      margin: 0 0 8px 0;
      font-size: 1.5em;
      font-weight: 600;
    }
    .login-card p {
      margin: 0 0 24px 0;
      opacity: 0.7;
      font-size: 0.9em;
    }
    .form-group {
      margin-bottom: 16px;
    }
    .form-group label {
      display: block;
      margin-bottom: 6px;
      font-size: 0.85em;
      font-weight: 500;
      opacity: 0.8;
    }
    .form-group input {
      width: 100%;
      padding: 10px 12px;
      border: 1px solid var(--border-primary, #333);
      border-radius: 6px;
      background: var(--bg-primary, #0a0a0a);
      color: var(--text-primary, #e0e0e0);
      font-size: 1em;
      box-sizing: border-box;
    }
    .form-group input:focus {
      outline: none;
      border-color: var(--accent, #4a9eff);
    }
    .login-btn {
      width: 100%;
      padding: 12px;
      border: none;
      border-radius: 6px;
      background: var(--accent, #4a9eff);
      color: white;
      font-size: 1em;
      font-weight: 500;
      cursor: pointer;
      margin-top: 8px;
    }
    .login-btn:hover { opacity: 0.9; }
    .error-msg {
      background: #2a0000;
      border: 1px solid #500000;
      border-radius: 6px;
      padding: 10px 12px;
      margin-bottom: 16px;
      font-size: 0.9em;
      display: none;
    }
    .error-msg.visible { display: block; }
  </style>
</head>
<body>
  <div class="login-card">
    <h1>Amplifier Distro</h1>
    <p>Sign in with your system account</p>
    <div id="error" class="error-msg"></div>
    <form id="login-form" method="post" action="/login">
      <div class="form-group">
        <label for="username">Username</label>
        <input type="text" id="username" name="username" autocomplete="username" required autofocus>
      </div>
      <div class="form-group">
        <label for="password">Password</label>
        <input type="password" id="password" name="password" autocomplete="current-password" required>
      </div>
      <button type="submit" class="login-btn">Sign in</button>
    </form>
  </div>
  <script>
    const form = document.getElementById('login-form');
    const errorDiv = document.getElementById('error');

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      errorDiv.classList.remove('visible');

      const formData = new FormData(form);
      try {
        const resp = await fetch('/login', {
          method: 'POST',
          body: formData,
          redirect: 'manual',
        });
        if (resp.type === 'opaqueredirect' || resp.status === 303) {
          window.location.href = '/';
        } else if (resp.status === 401) {
          errorDiv.textContent = 'Authentication failed. Check your username and password.';
          errorDiv.classList.add('visible');
        } else {
          errorDiv.textContent = 'Unexpected error. Please try again.';
          errorDiv.classList.add('visible');
        }
      } catch (err) {
        errorDiv.textContent = 'Network error. Please try again.';
        errorDiv.classList.add('visible');
      }
    });
  </script>
</body>
</html>
```

**Step 2: Verify the file is served correctly**

```bash
cd distro-server && python -c "
from pathlib import Path
p = Path('src/amplifier_distro/server/static/login.html')
assert p.exists(), 'login.html not found'
content = p.read_text()
assert 'Amplifier Distro' in content
assert 'username' in content
assert 'password' in content
print('login.html OK')
"
```
Expected: `login.html OK`

**Step 3: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: add login page HTML

Static login page matching Amplifier dark theme. Uses system account
credentials (PAM auth). Form submits via fetch with error handling.

Ref: #68"
```

---

### Task 15: Add require_auth Middleware

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/auth.py`
- Modify: `distro-server/tests/test_auth.py`

**Context:** The `require_auth` middleware checks for a valid session cookie on every request except: `/login`, `/logout`, `/api/health`, `/favicon.svg`, and static assets. Localhost requests bypass auth entirely. Bearer token (`AMPLIFIER_SERVER_API_KEY`) continues to work.

**Step 1: Write the failing test**

Add to `distro-server/tests/test_auth.py`:

```python
from amplifier_distro.server.auth import is_localhost_request


class TestIsLocalhostRequest:
    """Tests for localhost bypass detection."""

    def test_127_0_0_1_is_localhost(self):
        assert is_localhost_request("127.0.0.1") is True

    def test_localhost_string_is_localhost(self):
        assert is_localhost_request("localhost") is True

    def test_ipv6_loopback_is_localhost(self):
        assert is_localhost_request("::1") is True

    def test_remote_ip_is_not_localhost(self):
        assert is_localhost_request("192.168.1.100") is False

    def test_none_is_not_localhost(self):
        assert is_localhost_request(None) is False
```

**Step 2: Run test to verify it fails**

```bash
cd distro-server && python -m pytest tests/test_auth.py::TestIsLocalhostRequest -v
```
Expected: FAIL — `ImportError: cannot import name 'is_localhost_request'`

**Step 3: Implement localhost detection and auth middleware factory**

Add to `distro-server/src/amplifier_distro/server/auth.py`:

```python
# ---------------------------------------------------------------------------
# Localhost detection
# ---------------------------------------------------------------------------

_LOCALHOST_ADDRS = {"127.0.0.1", "localhost", "::1"}


def is_localhost_request(client_host: str | None) -> bool:
    """Check if a request comes from localhost.

    Localhost requests bypass auth entirely.
    """
    if client_host is None:
        return False
    return client_host in _LOCALHOST_ADDRS


# ---------------------------------------------------------------------------
# Auth middleware factory
# ---------------------------------------------------------------------------

# Paths that never require auth
_PUBLIC_PATHS = {"/login", "/logout", "/api/health", "/favicon.svg"}
_PUBLIC_PREFIXES = ("/static/",)


def create_auth_middleware(secret: str, session_timeout: int = 2592000):
    """Create a Starlette middleware that enforces authentication.

    Bypass rules (in order):
    1. Localhost requests — always allowed
    2. Public paths — /login, /logout, /api/health, static assets
    3. Valid AMPLIFIER_SERVER_API_KEY bearer token
    4. Valid session cookie

    If none of the above, redirect to /login (for HTML) or return 401 (for API).
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse, RedirectResponse

    class AuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            # 1. Localhost bypass
            client_host = request.client.host if request.client else None
            if is_localhost_request(client_host):
                return await call_next(request)

            # 2. Public paths
            path = request.url.path
            if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
                return await call_next(request)

            # 3. Bearer token (existing AMPLIFIER_SERVER_API_KEY)
            import hmac as _hmac
            api_key = os.environ.get("AMPLIFIER_SERVER_API_KEY", "")
            if api_key:
                auth_header = request.headers.get("authorization", "")
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]
                    if _hmac.compare_digest(token, api_key):
                        return await call_next(request)

            # 4. Session cookie
            cookie = request.cookies.get("amplifier_session")
            if cookie:
                username = verify_session_token(
                    cookie, secret=secret, max_age=session_timeout
                )
                if username is not None:
                    return await call_next(request)

            # Not authenticated — redirect or 401
            if "text/html" in request.headers.get("accept", ""):
                return RedirectResponse(url="/login", status_code=303)
            return JSONResponse(
                status_code=401,
                content={"error": "Authentication required"},
            )

    return AuthMiddleware
```

**Step 4: Run test to verify it passes**

```bash
cd distro-server && python -m pytest tests/test_auth.py -v
```
Expected: All PASS

**Step 5: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: add auth middleware with localhost bypass

- is_localhost_request(): detect 127.0.0.1, localhost, ::1
- create_auth_middleware(): Starlette middleware enforcing auth
  - Localhost always bypasses
  - Public paths excluded (/login, /api/health, /static/)
  - Bearer token (AMPLIFIER_SERVER_API_KEY) continues working
  - Session cookie checked last
  - HTML requests redirect to /login, API gets 401

Ref: #68"
```

---

### Task 16: Wire Auth into Server Startup

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/app.py`
- Modify: `distro-server/src/amplifier_distro/server/cli.py`
- Modify: `distro-server/tests/test_auth.py`

**Context:** The `DistroServer` class in `app.py` (line 91) creates the FastAPI app. We need to conditionally add auth middleware and routes when auth is applicable. The `_run_foreground` function in `cli.py` creates the server and should pass auth config through.

**Step 1: Write the failing test**

Add to `distro-server/tests/test_auth.py`:

```python
class TestAuthWiringConditional:
    """Auth is only wired when TLS is active on Linux."""

    def test_no_auth_middleware_when_tls_off(self):
        """Default server (no TLS) has no auth middleware."""
        from amplifier_distro.server.app import DistroServer

        server = DistroServer()
        middleware_types = [type(m).__name__ for m in server.app.user_middleware]
        assert "AuthMiddleware" not in str(middleware_types)

    def test_login_route_exists_when_auth_enabled(self):
        from amplifier_distro.server.app import DistroServer

        server = DistroServer(auth_secret="test-secret")
        route_paths = [getattr(r, "path", None) for r in server.app.routes]
        assert "/login" in route_paths
```

**Step 2: Run test to verify it fails**

```bash
cd distro-server && python -m pytest tests/test_auth.py::TestAuthWiringConditional -v
```
Expected: FAIL — `DistroServer() got an unexpected keyword argument 'auth_secret'`

**Step 3: Add auth_secret parameter to DistroServer**

In `distro-server/src/amplifier_distro/server/app.py`, modify the `DistroServer.__init__` method (line 94-126) to accept an optional `auth_secret` parameter:

Change the signature from:
```python
    def __init__(
        self,
        title: str = "Amplifier Distro",
        version: str = "0.1.0",
        dev_mode: bool = False,
        host: str = "0.0.0.0",
    ) -> None:
```
to:
```python
    def __init__(
        self,
        title: str = "Amplifier Distro",
        version: str = "0.1.0",
        dev_mode: bool = False,
        host: str = "0.0.0.0",
        auth_secret: str = "",
        session_timeout: int = 2592000,
    ) -> None:
```

After the line `self._app.state.host = host` (line 110), add:

```python
        # Auth routes and middleware (conditional)
        if auth_secret:
            from amplifier_distro.server.auth import create_auth_middleware
            from amplifier_distro.server.auth_routes import create_auth_router

            auth_router = create_auth_router(
                secret=auth_secret,
                session_timeout=session_timeout,
            )
            self._app.include_router(auth_router)

            middleware_cls = create_auth_middleware(
                secret=auth_secret,
                session_timeout=session_timeout,
            )
            self._app.add_middleware(middleware_cls)
```

Now update `_run_foreground` in `server/cli.py` to pass auth config. After the TLS resolution block (added in Task 9), add:

```python
    # Auth setup (conditional: TLS + Linux + enabled)
    from amplifier_distro.server.auth import get_or_create_secret, is_auth_applicable
    from amplifier_distro.distro_settings import load as load_settings

    settings = load_settings()
    auth_secret = ""
    if tls_paths is not None and is_auth_applicable(
        tls_active=True,
        auth_enabled=settings.server.auth.enabled,
    ):
        auth_secret = get_or_create_secret()
        logger.info("PAM authentication enabled")
```

Then update the `create_server` call to pass auth_secret. Change:
```python
    server = create_server(dev_mode=dev, host=host)
```
to:
```python
    server = create_server(dev_mode=dev, host=host, auth_secret=auth_secret)
```

Also update the `create_server` factory function at the bottom of `app.py` to accept and forward `auth_secret`:

```python
def create_server(dev_mode: bool = False, **kwargs: Any) -> DistroServer:
    """Factory function to create and configure the server."""
    return DistroServer(dev_mode=dev_mode, **kwargs)
```

(This already accepts `**kwargs`, so it will forward `auth_secret` automatically.)

**Step 4: Run test to verify it passes**

```bash
cd distro-server && python -m pytest tests/test_auth.py::TestAuthWiringConditional -v
```
Expected: All PASS

**Step 5: Run full test suite for regressions**

```bash
cd distro-server && python -m pytest tests/ -x -v --timeout=60
```
Expected: All PASS (auth_secret="" by default = no auth = backward-compatible)

**Step 6: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: wire auth into server startup (conditional on TLS + Linux)

DistroServer accepts auth_secret parameter. When set:
- Auth routes (/login, /logout, /auth/me) are mounted
- Auth middleware enforces login for all routes
- Localhost always bypasses auth

_run_foreground conditionally enables auth based on TLS + platform.
Default behavior (no TLS) = no auth = zero breaking changes.

Ref: #68"
```

---

## Phase 5: Doctor & Integration

### Task 17: Add Doctor Checks

**Files:**
- Modify: `distro-server/src/amplifier_distro/doctor.py`
- Modify: `distro-server/tests/test_doctor.py`

**Context:** Doctor checks use the `DiagnosticCheck` Pydantic model (line 45-52 of `doctor.py`) with `name`, `status`, `message`, `fix_available`, `fix_description`. Individual checks are `_check_*` functions that return a `DiagnosticCheck`. They're added to the report in `run_diagnostics()` (line 458-501). Test pattern: see `TestDiagnosticCheckModel` in `test_doctor.py`.

**Step 1: Write the failing test**

Add to `distro-server/tests/test_doctor.py`:

```python
class TestSecurityChecks:
    """Doctor checks for TLS/auth infrastructure."""

    def test_shadow_group_check_exists(self):
        """The _check_shadow_group function exists."""
        from amplifier_distro.doctor import _check_shadow_group

        result = _check_shadow_group()
        assert isinstance(result, DiagnosticCheck)
        assert result.name == "Shadow group"

    def test_certs_check_exists(self):
        """The _check_tls_certs function exists."""
        from amplifier_distro.doctor import _check_tls_certs

        result = _check_tls_certs()
        assert isinstance(result, DiagnosticCheck)
        assert result.name == "TLS certificates"

    def test_tailscale_check_exists(self):
        """The _check_tailscale function exists."""
        from amplifier_distro.doctor import _check_tailscale

        result = _check_tailscale()
        assert isinstance(result, DiagnosticCheck)
        assert result.name == "Tailscale"

    def test_shadow_group_skipped_on_macos(self):
        with patch("amplifier_distro.doctor.platform.system", return_value="Darwin"):
            from amplifier_distro.doctor import _check_shadow_group

            result = _check_shadow_group()
            assert result.status == CheckStatus.ok
            assert "skipped" in result.message.lower() or "macos" in result.message.lower()
```

**Step 2: Run test to verify it fails**

```bash
cd distro-server && python -m pytest tests/test_doctor.py::TestSecurityChecks -v
```
Expected: FAIL — `ImportError: cannot import name '_check_shadow_group'`

**Step 3: Implement the three new checks**

In `distro-server/src/amplifier_distro/doctor.py`, add these check functions after `_check_voice_configured` (after line 450) and before the `run_diagnostics` function:

```python
def _check_shadow_group() -> DiagnosticCheck:
    """Check if the current user is in the shadow group (Linux only).

    Required for PAM authentication to read /etc/shadow.
    """
    if platform.system() != "Linux":
        return DiagnosticCheck(
            name="Shadow group",
            status=CheckStatus.ok,
            message="Check skipped (not Linux)",
        )
    try:
        import grp
        import os as _os

        username = _os.getlogin()
        shadow_group = grp.getgrnam("shadow")
        if username in shadow_group.gr_mem:
            return DiagnosticCheck(
                name="Shadow group",
                status=CheckStatus.ok,
                message=f"User '{username}' is in the shadow group",
            )
        return DiagnosticCheck(
            name="Shadow group",
            status=CheckStatus.warning,
            message=f"User '{username}' is NOT in the shadow group",
            fix_available=False,
            fix_description=f"Run: sudo usermod -aG shadow {username}",
        )
    except (KeyError, OSError):
        return DiagnosticCheck(
            name="Shadow group",
            status=CheckStatus.warning,
            message="Could not check shadow group membership",
        )


def _check_tls_certs() -> DiagnosticCheck:
    """Check if TLS certificates are present and valid."""
    from amplifier_distro.distro_settings import load as _load_settings

    settings = _load_settings()
    if settings.server.tls.mode == "off":
        return DiagnosticCheck(
            name="TLS certificates",
            status=CheckStatus.ok,
            message="TLS not configured (mode: off)",
        )

    certs_dir = Path(conventions.DISTRO_CERTS_DIR).expanduser()
    if settings.server.tls.mode == "manual":
        certfile = Path(settings.server.tls.certfile)
        keyfile = Path(settings.server.tls.keyfile)
        if certfile.exists() and keyfile.exists():
            return DiagnosticCheck(
                name="TLS certificates",
                status=CheckStatus.ok,
                message=f"Manual certs: {certfile}",
            )
        return DiagnosticCheck(
            name="TLS certificates",
            status=CheckStatus.error,
            message="Manual cert/key file not found",
        )

    # mode == "auto"
    if any(certs_dir.glob("*.crt")) or any(certs_dir.glob("*.pem")):
        return DiagnosticCheck(
            name="TLS certificates",
            status=CheckStatus.ok,
            message=f"Certs found in {certs_dir}",
        )
    return DiagnosticCheck(
        name="TLS certificates",
        status=CheckStatus.warning,
        message=f"No certs in {certs_dir} — will be generated on next 'serve --tls auto'",
    )


def _check_tailscale() -> DiagnosticCheck:
    """Check if Tailscale is available and connected (informational)."""
    from amplifier_distro.tailscale import get_dns_name

    dns = get_dns_name()
    if dns:
        return DiagnosticCheck(
            name="Tailscale",
            status=CheckStatus.ok,
            message=f"Connected: {dns}",
        )
    if shutil.which("tailscale"):
        return DiagnosticCheck(
            name="Tailscale",
            status=CheckStatus.warning,
            message="Installed but not connected",
        )
    return DiagnosticCheck(
        name="Tailscale",
        status=CheckStatus.ok,
        message="Not installed (optional — for HTTPS remote access)",
    )
```

Now add these checks to `run_diagnostics()`. Find the `# Integration checks` section (around line 498) and add the new checks after the voice check:

```python
    # Security checks
    report.checks.append(_check_shadow_group())
    report.checks.append(_check_tls_certs())
    report.checks.append(_check_tailscale())
```

**Step 4: Run test to verify it passes**

```bash
cd distro-server && python -m pytest tests/test_doctor.py::TestSecurityChecks -v
```
Expected: All PASS

**Step 5: Run full doctor tests for regressions**

```bash
cd distro-server && python -m pytest tests/test_doctor.py -v
```
Expected: All PASS

**Step 6: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: add doctor checks for shadow group, TLS certs, Tailscale

Three new diagnostic checks in amp-distro doctor:
- Shadow group: checks if user can read /etc/shadow (Linux only)
- TLS certificates: checks cert presence based on TLS mode
- Tailscale: informational check for connectivity

Ref: #68"
```

---

### Task 18: Integration Tests

**Files:**
- Create: `distro-server/tests/test_secure_remote_integration.py`

**Context:** Final integration tests verify the full stack works together. Pattern: httpx.AsyncClient with ASGITransport (see `conftest.py:async_webchat_client`). These tests verify the auth flow end-to-end with mocked PAM, and that the origin allow-list works through actual HTTP requests.

**Step 1: Write the integration tests**

Create `distro-server/tests/test_secure_remote_integration.py`:

```python
"""Integration tests for secure remote access features.

End-to-end tests verifying the full auth flow, origin checking,
and TLS configuration through the actual ASGI server.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx

from amplifier_distro.server.app import DistroServer


def _make_server(auth_secret: str = "") -> DistroServer:
    """Create a DistroServer with optional auth enabled."""
    return DistroServer(auth_secret=auth_secret)


class TestOriginIntegration:
    """Origin checking works through actual HTTP requests."""

    async def test_health_endpoint_always_accessible(self):
        server = _make_server()
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/health")
            assert resp.status_code == 200

    async def test_server_without_auth_has_no_login_route(self):
        server = _make_server(auth_secret="")
        route_paths = [getattr(r, "path", None) for r in server.app.routes]
        assert "/login" not in route_paths

    async def test_server_with_auth_has_login_route(self):
        server = _make_server(auth_secret="test-secret-key")
        route_paths = [getattr(r, "path", None) for r in server.app.routes]
        assert "/login" in route_paths


class TestAuthFlowIntegration:
    """Full auth flow: login -> access -> logout."""

    async def test_unauthenticated_api_returns_401(self):
        server = _make_server(auth_secret="test-secret-key")
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/apps")
            assert resp.status_code == 401

    async def test_login_and_access_protected_route(self):
        server = _make_server(auth_secret="test-secret-key")
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Login
            with patch(
                "amplifier_distro.server.auth_routes.authenticate_pam",
                return_value=True,
            ):
                login_resp = await client.post(
                    "/login",
                    data={"username": "testuser", "password": "testpass"},
                    follow_redirects=False,
                )
                assert login_resp.status_code == 303

                # Extract cookie
                cookies = login_resp.cookies

            # Access protected route with cookie
            resp = await client.get("/api/health", cookies=cookies)
            assert resp.status_code == 200

    async def test_health_always_accessible_even_with_auth(self):
        """Health endpoint is public even when auth is enabled."""
        server = _make_server(auth_secret="test-secret-key")
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/health")
            assert resp.status_code == 200

    async def test_bearer_token_works_alongside_cookie_auth(self):
        """AMPLIFIER_SERVER_API_KEY still works when PAM auth is active."""
        import os

        server = _make_server(auth_secret="test-secret-key")
        transport = httpx.ASGITransport(app=server.app)

        os.environ["AMPLIFIER_SERVER_API_KEY"] = "test-api-key"
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/apps",
                    headers={"Authorization": "Bearer test-api-key"},
                )
                assert resp.status_code == 200
        finally:
            os.environ.pop("AMPLIFIER_SERVER_API_KEY", None)


class TestSettingsRoundTrip:
    """Settings load correctly with the new server section."""

    def test_default_settings_have_tls_off(self):
        from amplifier_distro.distro_settings import DistroSettings

        s = DistroSettings()
        assert s.server.tls.mode == "off"
        assert s.server.auth.enabled is True
        assert s.server.allowed_origins == []
```

**Step 2: Run the integration tests**

```bash
cd distro-server && python -m pytest tests/test_secure_remote_integration.py -v
```
Expected: All PASS

**Step 3: Run the full test suite**

```bash
cd distro-server && python -m pytest tests/ -v --timeout=60
```
Expected: All PASS

**Step 4: Commit**

```bash
cd distro-server && git add -A && git commit -m "feat: add integration tests for secure remote access

End-to-end tests covering:
- Auth flow (login -> cookie -> protected route)
- Bearer token works alongside cookie auth
- Health endpoint always public
- Origin checking through HTTP requests
- Settings round-trip with new server section

Ref: #68"
```

---

## Final Verification

After all tasks are complete, run the full test suite to verify no regressions:

```bash
cd distro-server && python -m pytest tests/ -v --timeout=60
```

Then verify the code quality:

```bash
cd distro-server && python -m ruff check src/ tests/
cd distro-server && python -m ruff format --check src/ tests/
```

Verify `amp-distro serve --help` shows the new flags:

```bash
cd distro-server && python -m amplifier_distro.cli serve --help
```

Expected output should include `--tls`, `--ssl-certfile`, `--ssl-keyfile`, `--no-auth`.
