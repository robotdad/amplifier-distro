"""Auth routes for amplifierd PAM authentication plugin.

Provides login, logout, and session verification endpoints using
PAM authentication and signed session cookies.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Cookie, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from auth_plugin.pam import (
    DEFAULT_SESSION_TIMEOUT,
    authenticate_pam,
    create_session_token,
    verify_session_token,
)

COOKIE_NAME = "amplifier_session"

_STATIC_DIR = Path(__file__).parent / "static"


def _login_html() -> str:
    """Return login page HTML from static file or inline fallback."""
    login_file = _STATIC_DIR / "login.html"
    if login_file.exists():
        return login_file.read_text()
    return (
        "<!DOCTYPE html><html><head><title>Login</title></head>"
        "<body><h1>Login</h1>"
        '<form method="post" action="/login">'
        '<label>Username <input name="username"></label><br>'
        '<label>Password <input name="password" type="password">'
        "</label><br>"
        '<button type="submit">Login</button>'
        "</form></body></html>"
    )


def _resolve_cookie_secure(request: Request) -> bool:
    """Resolve the secure flag for cookies from app settings.

    Reads ``settings.cookie_secure`` from ``request.app.state.settings``:
    - ``True`` (bool)  → ``True``
    - ``False`` (bool) → ``False``
    - ``"true"``       → ``True``
    - ``"false"``      → ``False``
    - ``"auto"``       → ``True`` when ``settings.tls_mode`` is not ``"off"``
                         or ``"tls_off"``; ``False`` otherwise
    - missing/error    → ``True`` (safe default)
    """
    try:
        settings = request.app.state.settings
        cookie_secure = settings.cookie_secure
    except AttributeError:
        return True  # safe default when no settings configured

    if isinstance(cookie_secure, bool):
        return cookie_secure
    if isinstance(cookie_secure, str):
        if cookie_secure.lower() == "true":
            return True
        if cookie_secure.lower() == "false":
            return False
        if cookie_secure.lower() == "auto":
            try:
                tls_mode = settings.tls_mode
            except AttributeError:
                return True  # safe default
            return tls_mode not in ("off", "tls_off")

    return True  # safe default for unexpected values


def _resolve_cookie_samesite(request: Request) -> str:
    """Resolve the samesite attribute for cookies from app settings.

    Reads ``settings.cookie_samesite`` from ``request.app.state.settings``,
    defaulting to ``"lax"`` if not set or on any error.
    """
    try:
        return str(request.app.state.settings.cookie_samesite)
    except AttributeError:
        return "lax"


def create_auth_router(
    secret: str,
    session_timeout: int = DEFAULT_SESSION_TIMEOUT,
) -> APIRouter:
    """Create an APIRouter with login/logout/auth-me routes."""
    router = APIRouter()

    @router.get("/login", response_class=HTMLResponse)
    async def login_page() -> HTMLResponse:
        """Serve the login HTML page."""
        return HTMLResponse(content=_login_html())

    @router.post("/login", response_model=None)
    async def login(request: Request) -> Response:
        """Authenticate via PAM and set a session cookie."""
        form = await request.form()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))

        if not authenticate_pam(username, password):
            return JSONResponse(
                status_code=401,
                content={"error": "Authentication failed"},
            )

        # Redirect back to the page that triggered the login, or / as fallback
        next_url = request.query_params.get("next", "/")
        # Open redirect prevention: only allow relative paths
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/"

        token = create_session_token(username, secret)
        response = RedirectResponse(url=next_url, status_code=303)
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            httponly=True,
            secure=_resolve_cookie_secure(request),
            samesite=_resolve_cookie_samesite(request),
            max_age=session_timeout,
        )
        return response

    @router.post("/logout", response_model=None)
    async def logout(request: Request) -> Response:
        """Clear the session cookie and redirect to /login."""
        response = RedirectResponse(url="/login", status_code=303)
        response.delete_cookie(
            key=COOKIE_NAME,
            path="/",
            secure=_resolve_cookie_secure(request),
            samesite=_resolve_cookie_samesite(request),
        )
        return response

    @router.get("/auth/me", response_model=None)
    async def auth_me(
        amplifier_session: str | None = Cookie(default=None),
    ) -> Response:
        """Return the current user or 401 if not authenticated."""
        if amplifier_session is None:
            return JSONResponse(status_code=401, content={"error": "Not authenticated"})

        username = verify_session_token(
            amplifier_session, secret, max_age=session_timeout
        )
        if username is None:
            return JSONResponse(
                status_code=401, content={"error": "Invalid or expired session"}
            )

        return JSONResponse(content={"username": username})

    return router
