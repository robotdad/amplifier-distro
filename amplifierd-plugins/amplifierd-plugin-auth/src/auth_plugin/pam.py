"""PAM authentication and session token helpers.

Provides Linux PAM authentication and session token management using
signed tokens (itsdangerous).
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

try:
    import pam as _pam
except ImportError:
    _pam = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_SESSION_TIMEOUT: int = 30 * 24 * 60 * 60  # 30 days in seconds

_SECRET_FILENAME = "session-secret.key"  # noqa: S105
_DEFAULT_SECRET_DIR = Path("~/.amplifier-distro").expanduser()


def authenticate_pam(username: str, password: str) -> bool:
    """Authenticate *username* / *password* via Linux PAM.

    Returns ``True`` on success, ``False`` on failure or when the ``pam``
    module is not installed.
    """
    if _pam is None:
        logger.warning("PAM module not available; authentication denied")
        return False

    p = _pam.pam()
    if p.authenticate(username, password):
        return True

    logger.warning("PAM authentication failed for user %s: %s", username, p.reason)
    return False


def get_or_create_secret(secret_dir: Path | None = None) -> str:
    """Read or create a session secret from *secret_dir* / ``session-secret.key``.

    If the file does not exist a new 32-byte hex token is generated and
    written with permissions ``0o600``.
    """
    if secret_dir is None:
        secret_dir = _DEFAULT_SECRET_DIR

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
    """Verify *token* and return the username, or ``None`` on failure."""
    signer = TimestampSigner(secret)
    try:
        return signer.unsign(token, max_age=max_age).decode()
    except (BadSignature, SignatureExpired):
        return None
