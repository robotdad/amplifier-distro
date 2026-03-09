"""FastAPI routes for the distro plugin.

Provides the ``create_routes()`` factory that returns an ``APIRouter`` with
a ``/distro`` prefix.  All endpoints retrieve ``DistroPluginSettings`` from
``request.app.state.distro.settings``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypedDict


from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from starlette.responses import FileResponse, HTMLResponse

from distro_plugin.config import DistroPluginSettings
from distro_plugin.distro_settings import (
    load as load_distro_settings,
    settings_path as distro_settings_path,
    update as update_distro_settings,
)
from distro_plugin.features import FEATURES, features_for_tier, get_enabled_features
from distro_plugin.overlay import (
    add_include,
    get_includes,
    overlay_exists,
    remove_include,
)
from distro_plugin.providers import (
    PROVIDERS,
    get_provider_catalog,
    handle_provider_request,
    sync_providers,
)

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


class BridgeInfo(TypedDict):
    """Typed structure for a bridge detection result."""

    name: str
    description: str
    available: bool


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------


class WelcomeData(BaseModel):
    workspace_root: str = ""
    github_handle: str = ""
    git_email: str = ""


class ModulesData(BaseModel):
    modules: list[str] = []


class ProviderRequest(BaseModel):
    provider: str = ""
    api_key: str = ""


class InterfacesData(BaseModel):
    interfaces: list[str] = []


class FeatureToggle(BaseModel):
    feature_id: str
    enabled: bool


class TierRequest(BaseModel):
    tier: int


class DistroSettingsUpdate(BaseModel):
    section: str | None = None
    values: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Bridge definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BridgeDefinition:
    """Typed structure for a bridge integration."""

    name: str
    description: str
    env_key: str


BRIDGE_DEFINITIONS: dict[str, BridgeDefinition] = {
    "slack": BridgeDefinition(
        name="Slack",
        description="Real-time Slack integration",
        env_key="SLACK_BOT_TOKEN",
    ),
    "discord": BridgeDefinition(
        name="Discord",
        description="Discord bot integration",
        env_key="DISCORD_BOT_TOKEN",
    ),
    "voice": BridgeDefinition(
        name="Voice",
        description="Voice interface via OpenAI Realtime API",
        env_key="OPENAI_API_KEY",
    ),
}

# Candidate directory names to scan under $HOME for workspace detection.
_WORKSPACE_CANDIDATES = ("projects", "repos", "src", "code", "dev", "workspace")


# ---------------------------------------------------------------------------
# Cross-cutting helpers
# ---------------------------------------------------------------------------


def compute_phase(settings: DistroPluginSettings) -> str:
    """Determine the current setup phase based on overlay and provider state.

    Returns one of: ``'unconfigured'``, ``'detected'``, ``'ready'``.
    """
    if not overlay_exists(settings):
        return "unconfigured"

    # Check if any provider env var is set
    for provider in PROVIDERS.values():
        if os.environ.get(provider.env_var):
            return "ready"

    return "detected"


def detect_bridges(settings: DistroPluginSettings) -> dict[str, BridgeInfo]:
    """Check which bridges have their required env keys set."""
    # settings reserved for future config-based bridge detection
    result: dict[str, BridgeInfo] = {}
    for bridge_id, bridge in BRIDGE_DEFINITIONS.items():
        result[bridge_id] = BridgeInfo(
            name=bridge.name,
            description=bridge.description,
            available=bool(os.environ.get(bridge.env_key)),
        )
    return result


def _get_current_provider(settings: DistroPluginSettings) -> dict[str, Any] | None:
    """Return the current primary provider info by matching overlay URIs."""
    current_uris = set(get_includes(settings))
    for pid, provider in PROVIDERS.items():
        if provider.include in current_uris:
            has_key = bool(os.environ.get(provider.env_var))
            return {
                "id": pid,
                "name": provider.name,
                "model": provider.default_model,
                "has_key": has_key,
            }
    return None


def _build_status(settings: DistroPluginSettings) -> dict[str, Any]:
    """Compose the full status response dict."""
    phase = compute_phase(settings)
    provider = _get_current_provider(settings)
    features = get_enabled_features(settings)
    bridges = detect_bridges(settings)

    feature_details = []
    for fid in features:
        feat = FEATURES.get(fid)
        if feat:
            feature_details.append(
                {
                    "id": feat.id,
                    "name": feat.name,
                    "tier": feat.tier,
                    "category": feat.category,
                }
            )

    return {
        "phase": phase,
        "provider": provider,
        "features": feature_details,
        "bridges": bridges,
    }


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _uv_tool_install(package: str) -> tuple[bool, str]:
    """Run ``uv tool install <package>`` asynchronously.

    Returns ``(success, output)``."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "uv",
            "tool",
            "install",
            package,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = (stdout or b"").decode() + (stderr or b"").decode()
        return proc.returncode == 0, output.strip()
    except FileNotFoundError:
        return False, "uv not found"


# ---------------------------------------------------------------------------
# Subprocess helpers for detection
# ---------------------------------------------------------------------------


async def _run_command(*args: str) -> str:
    """Run a subprocess and return stripped stdout, or '' on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return (stdout or b"").decode().strip()
    except (FileNotFoundError, OSError):
        pass
    return ""


# ---------------------------------------------------------------------------
# Route factory
# ---------------------------------------------------------------------------


def _get_settings(request: Request) -> DistroPluginSettings:
    """Extract settings from the app state."""
    return request.app.state.distro.settings


def create_routes() -> APIRouter:
    """Create and return the distro plugin routes.

    Returns a root-level APIRouter that:
    - Mounts ``StaticFiles`` at ``/static`` for shared CSS/JS assets
    - Serves ``/favicon.svg`` directly
    - Includes all ``/distro/*`` API and HTML page routes
    """
    # Root-level router (no prefix) – serves static assets at app root paths
    # so that HTML pages can reference /static/… and /favicon.svg without any
    # path prefix.
    outer = APIRouter()

    @outer.get("/favicon.svg", include_in_schema=False)
    async def get_favicon() -> FileResponse:
        """Serve the SVG favicon from the bundled static directory."""
        return FileResponse(_STATIC_DIR / "favicon.svg")

    @outer.get("/static/{file_path:path}", include_in_schema=False)
    async def serve_static(file_path: str) -> FileResponse:
        """Serve static assets (CSS, JS, fonts) from the bundled static directory."""
        full_path = _STATIC_DIR / file_path
        if not full_path.is_file() or not full_path.resolve().is_relative_to(
            _STATIC_DIR.resolve()
        ):
            raise HTTPException(status_code=404)
        return FileResponse(full_path)

    # All /distro/* routes live on a prefixed sub-router included below.
    router = APIRouter(prefix="/distro")

    @router.get("/status")
    async def get_status(request: Request) -> dict[str, Any]:
        """Return current distro setup status."""
        settings = _get_settings(request)
        return _build_status(settings)

    @router.get("/detect")
    async def get_detect(request: Request) -> dict[str, Any]:
        """Run environment detection and return comprehensive results."""
        settings = _get_settings(request)

        # Run subprocess detections concurrently
        gh_user, git_name, git_email = await asyncio.gather(
            _run_command("gh", "api", "user", "-q", ".login"),
            _run_command("git", "config", "--global", "user.name"),
            _run_command("git", "config", "--global", "user.email"),
        )

        github = {
            "user": gh_user,
            "authenticated": bool(gh_user),
        }

        git = {
            "name": git_name,
            "email": git_email,
            "configured": bool(git_name or git_email),
        }

        # Check env vars for API keys
        api_keys: dict[str, bool] = {}
        for pid, provider in PROVIDERS.items():
            api_keys[pid] = bool(os.environ.get(provider.env_var))

        # Scan workspace candidates
        workspace_candidates: list[str] = []
        home = os.path.expanduser("~")
        for candidate in _WORKSPACE_CANDIDATES:
            candidate_path = os.path.join(home, candidate)
            if os.path.isdir(candidate_path):
                workspace_candidates.append(candidate_path)

        bridges = detect_bridges(settings)

        return {
            "github": github,
            "git": git,
            "api_keys": api_keys,
            "workspace_candidates": workspace_candidates,
            "bridges": bridges,
            # Flat convenience fields
            "github_user": gh_user,
            "git_name": git_name,
            "git_email": git_email,
        }

    # ------------------------------------------------------------------
    # Provider & feature management endpoints
    # ------------------------------------------------------------------

    @router.get("/providers")
    async def get_providers(request: Request) -> dict[str, Any]:
        """Return the full provider catalog with configuration status."""
        settings = _get_settings(request)
        return {"providers": get_provider_catalog(settings)}

    @router.post("/provider")
    async def post_provider(request: Request, body: ProviderRequest) -> dict[str, Any]:
        """Detect and register a provider from an API key or provider ID."""
        settings = _get_settings(request)
        result = handle_provider_request(
            settings, provider=body.provider, api_key=body.api_key
        )
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("detail", ""))
        return result

    @router.get("/modules")
    async def get_modules(request: Request) -> dict[str, Any]:
        """Return the feature catalog with enabled status for each feature."""
        settings = _get_settings(request)
        enabled = set(get_enabled_features(settings))
        modules = [
            {
                "id": feat.id,
                "name": feat.name,
                "description": feat.description,
                "tier": feat.tier,
                "category": feat.category,
                "enabled": feat.id in enabled,
            }
            for feat in FEATURES.values()
        ]
        return {"modules": modules}

    @router.post("/features")
    async def post_features(request: Request, body: FeatureToggle) -> dict[str, Any]:
        """Toggle an individual feature on or off."""
        settings = _get_settings(request)
        feature = FEATURES.get(body.feature_id)
        if not feature:
            raise HTTPException(
                status_code=400, detail=f"Unknown feature: {body.feature_id}"
            )

        if body.enabled:
            # Enable: add includes for the feature and its direct dependencies.
            # NOTE: only single-depth resolution — if a dependency itself had
            # dependencies they would not be added.  The current catalog has at
            # most one level of requires, so this is sufficient.
            for dep_id in feature.requires:
                dep = FEATURES.get(dep_id)
                if dep:
                    for inc in dep.includes:
                        add_include(settings, inc)
            for inc in feature.includes:
                add_include(settings, inc)
        else:
            # Disable: remove only this feature's includes (dependencies may be
            # shared by other enabled features, so leave them in place).
            for inc in feature.includes:
                remove_include(settings, inc)

        return {"status": "ok", "feature_id": body.feature_id, "enabled": body.enabled}

    @router.post("/tier")
    async def post_tier(request: Request, body: TierRequest) -> dict[str, Any]:
        """Enable all features up to the given tier."""
        settings = _get_settings(request)
        # features_for_tier returns lower-tier features first (iterates
        # range(1, tier+1)), so dependencies are naturally added before
        # the features that require them.
        feature_ids = features_for_tier(body.tier)
        for fid in feature_ids:
            feat = FEATURES.get(fid)
            if feat:
                for inc in feat.includes:
                    add_include(settings, inc)
        return {"status": "ok", "tier": body.tier, "features_enabled": feature_ids}

    # ------------------------------------------------------------------
    # Setup wizard step endpoints
    # ------------------------------------------------------------------

    @router.post("/setup/steps/welcome")
    async def step_welcome(request: Request, body: WelcomeData) -> dict[str, Any]:
        """Save identity data from the welcome step."""
        settings = _get_settings(request)
        if body.workspace_root:
            update_distro_settings(settings, workspace_root=body.workspace_root)
        update_distro_settings(
            settings,
            section="identity",
            github_handle=body.github_handle,
            git_email=body.git_email,
        )
        return {"status": "ok"}

    @router.post("/setup/steps/config")
    async def step_config(request: Request) -> dict[str, Any]:
        """Config step passthrough."""
        return {"status": "ok"}

    @router.post("/setup/steps/modules")
    async def step_modules(request: Request, body: ModulesData) -> dict[str, Any]:
        """Enable requested features and disable unrequested ones."""
        settings = _get_settings(request)
        requested = set(body.modules)
        for fid, feat in FEATURES.items():
            if fid in requested:
                for dep_id in feat.requires:
                    dep = FEATURES.get(dep_id)
                    if dep:
                        for inc in dep.includes:
                            add_include(settings, inc)
                for inc in feat.includes:
                    add_include(settings, inc)
            else:
                for inc in feat.includes:
                    remove_include(settings, inc)
        return {"status": "ok"}

    @router.post("/setup/steps/interfaces")
    async def step_interfaces(request: Request, body: InterfacesData) -> dict[str, Any]:
        """Conditionally install CLI/TUI interfaces."""
        cli_installed = False
        tui_installed = False
        if "cli" in body.interfaces:
            ok, _ = await _uv_tool_install("amplifier-app-cli")
            cli_installed = ok
        if "tui" in body.interfaces:
            ok, _ = await _uv_tool_install("amplifier-app-tui")
            tui_installed = ok
        return {
            "status": "ok",
            "cli_installed": cli_installed,
            "tui_installed": tui_installed,
        }

    @router.post("/setup/steps/provider")
    async def step_provider(request: Request, body: ProviderRequest) -> dict[str, Any]:
        """Register a provider with explicit key or sync from environment."""
        settings = _get_settings(request)
        if body.api_key.strip() or body.provider.strip():
            result = handle_provider_request(
                settings, provider=body.provider, api_key=body.api_key
            )
            if result.get("status") == "error":
                raise HTTPException(status_code=400, detail=result.get("detail", ""))
            return result
        # Sync mode: auto-register providers from environment keys
        results = sync_providers(settings)
        return {"status": "ok", "synced": len(results)}

    @router.post("/setup/steps/verify")
    async def step_verify(request: Request) -> dict[str, Any]:
        """Compute phase and return comprehensive setup status."""
        settings = _get_settings(request)
        phase = compute_phase(settings)
        ov_exists = overlay_exists(settings)
        return {
            "phase": phase,
            "ready": phase == "ready",
            "overlay_exists": ov_exists,
        }

    # ------------------------------------------------------------------
    # Distro settings CRUD
    # ------------------------------------------------------------------

    @router.get("/distro-settings")
    async def get_distro_settings(request: Request) -> dict[str, Any]:
        """Return the current distro settings as a dict with the file path."""
        settings = _get_settings(request)
        ds = load_distro_settings(settings)
        return {
            "settings": asdict(ds),
            "path": str(distro_settings_path(settings)),
        }

    @router.post("/distro-settings")
    async def post_distro_settings(
        request: Request, body: DistroSettingsUpdate
    ) -> dict[str, Any]:
        """Update distro settings (root or section fields)."""
        settings = _get_settings(request)
        update_distro_settings(settings, section=body.section, **body.values)
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # Static HTML pages
    # ------------------------------------------------------------------

    @router.get("/setup")
    async def get_setup_page(request: Request) -> HTMLResponse:
        """Serve the setup wizard HTML page."""
        html_path = _STATIC_DIR / "wizard.html"
        try:
            content = html_path.read_text()
            return HTMLResponse(content=content)
        except OSError:
            return HTMLResponse(
                content="<h1>Setup UI not available</h1><p>Static files not found.</p>",
                status_code=500,
            )

    @router.get("/settings")
    async def get_settings_page(request: Request) -> HTMLResponse:
        """Serve the settings HTML page."""
        html_path = _STATIC_DIR / "settings.html"
        try:
            content = html_path.read_text()
            return HTMLResponse(content=content)
        except OSError:
            return HTMLResponse(
                content="<h1>Settings UI not available</h1><p>Static files not found.</p>",
                status_code=500,
            )

    outer.include_router(router)
    return outer
