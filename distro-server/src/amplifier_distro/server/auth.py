"""PAM authentication helpers for amplifier-distro.

Provides Linux PAM authentication and a guard that decides whether
authentication should be enforced based on TLS state, platform, and
configuration.
"""

from __future__ import annotations

import logging

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
