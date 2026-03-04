"""PAM authentication and session token helpers for amplifier-distro.

Provides Linux PAM authentication, a guard that decides whether
authentication should be enforced based on TLS state, platform, and
configuration, and session token management using signed tokens.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from amplifier_distro.conventions import DISTRO_HOME

try:
    import pam as _pam
except ImportError:
    _pam = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


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


def verify_session_token(token: str, secret: str, max_age: int = 2592000) -> str | None:
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
