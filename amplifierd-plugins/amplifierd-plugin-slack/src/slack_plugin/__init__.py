"""amplifierd-plugin-slack — Slack bridge for Amplifier sessions.

Connects Slack channels/threads to Amplifier sessions via the
amplifierd plugin system.

Architecture:
    Slack -> POST /slack/events -> SlackEventHandler
        -> CommandHandler (for @mentions and commands)
        -> SlackSessionManager -> SessionManagerAdapter -> amplifierd SessionManager
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse

from .backend_adapter import SessionManagerAdapter
from .client import MemorySlackClient, SlackClient
from .commands import CommandHandler
from .config import SlackConfig
from .discovery import AmplifierDiscovery
from .events import SlackEventHandler
from .sessions import SlackSessionManager
from .setup import router as setup_router
from .simulator import router as simulator_router
from .simulator import set_bridge_state, wire_client_to_hub

logger = logging.getLogger(__name__)

# Module-level bridge state, populated by create_router().
_state: dict[str, Any] = {}

# Shared aiohttp session for Socket Mode connections.
_slack_aiohttp_session: Any = None


def _get_state() -> dict[str, Any]:
    if not _state:
        raise RuntimeError("Slack bridge not initialized.")
    return _state


def create_router(state: Any) -> APIRouter:
    """Plugin entry point called by amplifierd to discover and mount routes."""
    router = APIRouter(prefix="/slack")

    # --- Configuration ---
    config = SlackConfig.from_env()

    # --- Discovery ---
    discovery = AmplifierDiscovery()

    # --- Client ---
    client: SlackClient
    if config.simulator_mode or not config.is_configured:
        logger.info("Slack bridge starting in simulator mode")
        client = MemorySlackClient()
        config.simulator_mode = True
    else:
        from .client import HttpSlackClient

        client = HttpSlackClient(config.bot_token)

    # --- Backend adapter wrapping amplifierd SessionManager ---
    backend = SessionManagerAdapter(state.session_manager)

    # --- Session persistence ---
    plugins_dir: Path = getattr(state.settings, "plugins_dir", Path.home() / ".amplifierd" / "plugins")
    persistence_path = (
        plugins_dir / "slack" / "slack-sessions.json"
        if not config.simulator_mode
        else None
    )

    # --- Core components ---
    session_manager = SlackSessionManager(client, backend, config, persistence_path)
    command_handler = CommandHandler(session_manager, discovery, config)
    event_handler = SlackEventHandler(client, session_manager, command_handler, config)

    bridge_state = {
        "config": config,
        "client": client,
        "backend": backend,
        "discovery": discovery,
        "session_manager": session_manager,
        "command_handler": command_handler,
        "event_handler": event_handler,
    }
    _state.update(bridge_state)

    # Wire simulator hub in simulator mode
    if config.simulator_mode and isinstance(client, MemorySlackClient):
        wire_client_to_hub(client)
        set_bridge_state(bridge_state)

    # --- Lifecycle events ---

    @router.on_event("startup")
    async def on_startup() -> None:
        global _slack_aiohttp_session
        logger.info("Slack bridge initialized (mode: %s)", config.mode)

        if config.socket_mode and config.is_configured:
            try:
                import aiohttp

                from .socket_mode import SocketModeAdapter

                _slack_aiohttp_session = aiohttp.ClientSession()
                adapter = SocketModeAdapter(
                    config, event_handler, session=_slack_aiohttp_session
                )
                _state["socket_adapter"] = adapter
                await adapter.start()
                logger.info("Socket Mode connection started")
            except ImportError:
                logger.warning(
                    "Socket Mode requires aiohttp: pip install amplifierd-plugin-slack[socket]"
                )
            except Exception:
                logger.exception("Socket Mode startup failed; Slack bridge degraded")

    @router.on_event("shutdown")
    async def on_shutdown() -> None:
        global _slack_aiohttp_session
        socket_adapter = _state.get("socket_adapter")
        if socket_adapter is not None:
            await socket_adapter.stop()

        for mapping in session_manager.list_active():
            try:
                await backend.end_session(mapping.session_id)
            except (RuntimeError, ValueError, ConnectionError, OSError):
                logger.exception("Error ending session %s", mapping.session_id)

        if _slack_aiohttp_session is not None and not _slack_aiohttp_session.closed:
            await _slack_aiohttp_session.close()
        _slack_aiohttp_session = None

        _state.clear()
        logger.info("Slack bridge shut down")

    # --- HTML Pages ---

    @router.get("/setup-ui", response_class=HTMLResponse)
    async def setup_page() -> HTMLResponse:
        html_file = Path(__file__).parent / "static" / "slack-setup.html"
        if html_file.exists():
            return HTMLResponse(content=html_file.read_text())
        return HTMLResponse(
            content="<h1>Slack Setup</h1><p>slack-setup.html not found.</p>",
            status_code=500,
        )

    # --- Slack Event Routes ---

    @router.post("/events")
    async def slack_events(request: Request) -> Response:
        s = _get_state()
        handler: SlackEventHandler = s["event_handler"]
        cfg: SlackConfig = s["config"]

        body = await request.body()

        if not cfg.simulator_mode:
            timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
            signature = request.headers.get("X-Slack-Signature", "")
            if not handler.verify_signature(body, timestamp, signature):
                return Response(status_code=401, content="Invalid signature")

        payload = json.loads(body)
        result = await handler.handle_event_payload(payload)
        return Response(
            content=json.dumps(result),
            media_type="application/json",
        )

    @router.post("/commands/{command}")
    async def slack_command(command: str, request: Request) -> Response:
        s = _get_state()
        cmd_handler: CommandHandler = s["command_handler"]

        form = await request.form()
        text = str(form.get("text", ""))
        user_id = str(form.get("user_id", ""))
        user_name = str(form.get("user_name", ""))
        channel_id = str(form.get("channel_id", ""))

        from .commands import CommandContext

        ctx = CommandContext(
            channel_id=channel_id,
            user_id=user_id,
            user_name=user_name,
            raw_text=text,
        )

        parts = text.split() if text else []
        sub_command = parts[0] if parts else command
        args = parts[1:] if len(parts) > 1 else []

        result = await cmd_handler.handle(sub_command, args, ctx)

        response_data: dict[str, Any] = {}
        if result.blocks:
            response_data["blocks"] = result.blocks
        if result.text:
            response_data["text"] = result.text
        if result.ephemeral:
            response_data["response_type"] = "ephemeral"
        else:
            response_data["response_type"] = "in_channel"

        return Response(
            content=json.dumps(response_data),
            media_type="application/json",
        )

    @router.post("/interactive")
    async def slack_interactive(request: Request) -> Response:
        s = _get_state()
        handler: SlackEventHandler = s["event_handler"]

        form = await request.form()
        payload_str = str(form.get("payload", "{}"))
        payload = json.loads(payload_str)

        await handler.handle_interactive_payload(payload)
        return Response(status_code=200)

    # --- Bridge Management API ---

    @router.get("/status")
    async def bridge_status() -> dict[str, Any]:
        s = _get_state()
        cfg: SlackConfig = s["config"]
        sm: SlackSessionManager = s["session_manager"]
        return {
            "status": "ok",
            "mode": cfg.mode,
            "hub_channel": cfg.hub_channel_name,
            "active_sessions": len(sm.list_active()),
            "is_configured": cfg.is_configured,
        }

    @router.get("/sessions")
    async def list_bridge_sessions() -> list[dict[str, Any]]:
        s = _get_state()
        sm: SlackSessionManager = s["session_manager"]
        return [
            {
                "session_id": m.session_id,
                "channel_id": m.channel_id,
                "thread_ts": m.thread_ts,
                "project_id": m.project_id,
                "description": m.description,
                "created_by": m.created_by,
                "is_active": m.is_active,
            }
            for m in sm.list_active()
        ]

    @router.get("/discover")
    async def discover_local_sessions(
        limit: int = 20,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        s = _get_state()
        disc: AmplifierDiscovery = s["discovery"]
        sessions = disc.list_sessions(limit=limit, project_filter=project)
        return [
            {
                "session_id": s.session_id,
                "project": s.project,
                "project_path": s.project_path,
                "date_str": s.date_str,
                "name": s.name,
                "description": s.description,
            }
            for s in sessions
        ]

    @router.get("/projects")
    async def list_projects() -> list[dict[str, Any]]:
        s = _get_state()
        disc: AmplifierDiscovery = s["discovery"]
        projects = disc.list_projects()
        return [
            {
                "project_id": p.project_id,
                "project_name": p.project_name,
                "project_path": p.project_path,
                "session_count": p.session_count,
                "last_active": p.last_active,
            }
            for p in projects
        ]

    # --- Include sub-routers ---
    router.include_router(setup_router)
    router.include_router(simulator_router)

    return router
