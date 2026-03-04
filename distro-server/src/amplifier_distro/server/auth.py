"""PAM authentication and session token helpers for amplifier-distro.

Provides Linux PAM authentication, a guard that decides whether
authentication should be enforced based on TLS state, platform, and
configuration, and session token management using signed tokens.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from amplifier_distro.conventions import DISTRO_HOME

try:
    import pam as _pam
except ImportError:
    _pam = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_SESSION_TIMEOUT: int = 30 * 24 * 60 * 60  # 30 days in seconds


def authenticate_pam(username: str, password: str) -> bool:
    """Authenticate *username* / *password* via Linux PAM.

    Returns ``True`` on success, ``False`` on failure or when the ``pam``
    module is not installed.  On failure the PAM reason is logged server-side
    but never exposed to the caller.
    """
    if _pam is None:
        logger.warning("PAM module not available; authentication denied")
        return False

    p = _pam.pam()
    if p.authenticate(username, password):
        return True

    logger.warning("PAM authentication failed for user %s: %s", username, p.reason)
    return False


def is_auth_applicable(
    tls_active: bool,
    platform: str | None = None,
    auth_enabled: bool = True,
) -> bool:
    """Return ``True`` only when all conditions for PAM auth are met.

    Conditions: TLS is active, platform is Linux, and auth is enabled.
    """
    if not tls_active:
        return False
    if platform != "linux":
        return False
    return auth_enabled


# ---------------------------------------------------------------------------
# Session token management
# ---------------------------------------------------------------------------

_SECRET_FILENAME = "session-secret.key"  # noqa: S105


def get_or_create_secret(secret_dir: Path | None = None) -> str:
    """Read or create a session secret from *secret_dir* / ``session-secret.key``.

    If the file does not exist a new 32-byte hex token is generated and
    written with permissions ``0o600``.  *secret_dir* defaults to
    :data:`~amplifier_distro.conventions.DISTRO_HOME`.
    """
    if secret_dir is None:
        secret_dir = Path(DISTRO_HOME).expanduser()

    secret_file = secret_dir / _SECRET_FILENAME

    if secret_file.exists():
        return secret_file.read_text().strip()

    secret_dir.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    secret_file.write_text(secret)
    secret_file.chmod(0o600)
    return secret


def create_session_token(username: str, secret: str) -> str:
    """Create a signed session token for *username*."""
    signer = TimestampSigner(secret)
    return signer.sign(username).decode()


def verify_session_token(
    token: str, secret: str, max_age: int = DEFAULT_SESSION_TIMEOUT
) -> str | None:
    """Verify *token* and return the username, or ``None`` on failure.

    *max_age* is the maximum token age in seconds (default 30 days).
    Returns ``None`` on :class:`~itsdangerous.BadSignature` or
    :class:`~itsdangerous.SignatureExpired`.
    """
    signer = TimestampSigner(secret)
    try:
        return signer.unsign(token, max_age=max_age).decode()
    except (BadSignature, SignatureExpired):
        return None


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
    import hmac as _hmac

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
            if path in _PUBLIC_PATHS or any(
                path.startswith(p) for p in _PUBLIC_PREFIXES
            ):
                return await call_next(request)

            # 3. Bearer token (existing AMPLIFIER_SERVER_API_KEY)
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
