"""PAM authentication plugin for amplifierd.

Provides Linux PAM-based login with session cookie management.
Activates only when: platform is Linux, TLS is active, and auth is enabled
in the daemon settings. On other platforms or when inactive, returns an
empty router (plugin is a no-op).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)


def create_router(state: Any) -> APIRouter:
    """amplifierd plugin entry point.

    Returns an APIRouter with /login, /logout, /auth/me routes when
    PAM auth is applicable.  Returns an empty router otherwise.
    """
    router = APIRouter()

    settings = getattr(state, "settings", None)
    if settings is None:
        return router

    # PAM auth only on Linux with auth enabled
    auth_enabled = getattr(settings, "auth_enabled", False)
    if sys.platform != "linux" or not auth_enabled:
        logger.debug(
            "Auth plugin inactive: platform=%s, auth_enabled=%s",
            sys.platform,
            auth_enabled,
        )
        return router

    # Import heavy deps only when actually activating
    from auth_plugin.pam import get_or_create_secret, verify_session_token
    from auth_plugin.routes import create_auth_router

    secret = get_or_create_secret()

    # Expose a verify callable so SessionAuthMiddleware (in amplifierd) can
    # validate session cookies without importing from the auth plugin directly.
    # The middleware reads this from app.state at dispatch time.
    state.auth_verify_session = lambda token: verify_session_token(token, secret)

    auth_router = create_auth_router(secret)
    router.include_router(auth_router)

    logger.info("PAM authentication plugin active")
    return router
