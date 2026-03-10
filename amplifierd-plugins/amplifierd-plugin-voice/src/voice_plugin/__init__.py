"""amplifierd-plugin-voice — Voice interface via OpenAI Realtime API.

Architecture:
    Browser (Preact SPA)
        -> WebRTC audio (Opus codec)
        -> OpenAI Realtime API (direct connection, no audio through server)
        <- Audio response streamed back via WebRTC

    Backend (this module):
        GET  /voice/                    - Voice UI
        GET  /voice/static/*           - Static assets
        GET  /voice/api/status         - Voice service status
        GET  /voice/session    (auth)  - GA ephemeral client_secret token
        POST /voice/sdp                - WebRTC SDP offer/answer exchange
        GET  /voice/events     (csrf) - SSE event stream
        POST /voice/sessions   (auth)  - Create Amplifier session
        POST /voice/sessions/{id}/resume (auth) - Reconnect
        POST /voice/sessions/{id}/transcript (auth) - Batch sync
        POST /voice/sessions/{id}/end  (auth) - End session
        GET  /voice/sessions   (auth)  - List conversations
        POST /voice/tools/execute (auth) - Execute voice tools
        POST /voice/cancel     (auth)  - Cancel running session

Single-user design: one _active_connection at a time.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)

from voice_plugin.backend_adapter import VoiceBackendAdapter
from voice_plugin.connection import VoiceConnection
from voice_plugin.transcript.models import TranscriptEntry, VoiceConversation
from voice_plugin.transcript.repository import VoiceConversationRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_active_connection: VoiceConnection | None = None
_allowed_origins: set[str] = {"localhost", "127.0.0.1"}
_backend_override: Any = None
_repo_override: Any = None
_backend_ref: VoiceBackendAdapter | None = None

_VALID_SESSION_ID = re.compile(r"^[a-zA-Z0-9_\-]+$")

# ---------------------------------------------------------------------------
# Origin / CSRF helpers (inlined from amplifier_distro.server.origins)
# ---------------------------------------------------------------------------


def _build_allowed_origins(extra: list[str] | None = None) -> list[str]:
    origins: list[str] = ["localhost", "127.0.0.1"]
    hostname = socket.gethostname()
    if hostname:
        origins.append(hostname)
    if extra:
        origins.extend(extra)
    return list(dict.fromkeys(origins))


def _is_origin_allowed(origin: str | None, allowed: set[str]) -> bool:
    if origin is None:
        return True
    return any(entry in origin for entry in allowed)


# ---------------------------------------------------------------------------
# Stub mode helpers (inlined from amplifier_distro.server.stub)
# ---------------------------------------------------------------------------

_stub_mode: bool = False


def _is_stub_mode() -> bool:
    return _stub_mode


def _stub_voice_client_secret() -> str:
    return "ek_stub_token_for_testing"


def _stub_voice_sdp() -> str:
    return "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=stub\r\nt=0 0\r\n"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_backend() -> VoiceBackendAdapter:
    if _backend_override is not None:
        return _backend_override
    if _backend_ref is not None:
        return _backend_ref
    raise RuntimeError("Voice plugin backend not initialized")


def _get_repo() -> VoiceConversationRepository:
    if _repo_override is not None:
        return _repo_override
    return VoiceConversationRepository()


def _get_voice_config() -> dict[str, Any]:
    return {
        "voice": os.environ.get("AMPLIFIER_VOICE_VOICE", "ash"),
        "model": os.environ.get("AMPLIFIER_VOICE_MODEL", "gpt-4o-realtime-preview"),
        "instructions": os.environ.get("AMPLIFIER_VOICE_INSTRUCTIONS", ""),
        "assistant_name": os.environ.get("AMPLIFIER_VOICE_ASSISTANT_NAME", "Amplifier"),
    }


def _get_workspace_root() -> Path:
    workspace = os.environ.get("AMPLIFIER_WORKSPACE_ROOT", "")
    if workspace:
        return Path(workspace).expanduser().resolve()
    return Path.home()


# ---------------------------------------------------------------------------
# Auth + CSRF
# ---------------------------------------------------------------------------


async def _require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    api_key = os.environ.get("AMPLIFIER_SERVER_API_KEY", "")
    if not api_key:
        return
    if x_api_key is None or not hmac.compare_digest(x_api_key, api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


async def _check_origin(origin: str | None = Header(default=None)) -> None:
    if not _is_origin_allowed(origin, _allowed_origins):
        raise HTTPException(status_code=403, detail="CSRF: origin not allowed")


def _validate_session_id(session_id: str) -> None:
    if not _VALID_SESSION_ID.match(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id format")


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def create_router(state: Any) -> APIRouter:
    """Plugin entry point called by amplifierd."""
    global _backend_ref, _allowed_origins, _stub_mode

    router = APIRouter(prefix="/voice")

    # Create backend adapter
    _backend_ref = VoiceBackendAdapter(state.session_manager)

    # Rebuild allowed origins
    _allowed_origins = set(_build_allowed_origins())

    # Check if stub mode via env
    _stub_mode = os.environ.get("AMPLIFIER_STUB_MODE", "").lower() in ("1", "true", "yes")

    logger.info("Voice plugin initialized (stub_mode=%s)", _stub_mode)

    # --- Static / UI ---

    @router.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html_path = Path(__file__).parent / "static" / "index.html"
        if html_path.exists():
            return HTMLResponse(content=html_path.read_text())
        return HTMLResponse(
            content=(
                "<!-- Voice UI not built yet -->"
                "<html><body><h1>Amplifier Voice</h1>"
                "<p>Run <code>npm run build</code> to build the UI.</p></body></html>"
            ),
            status_code=200,
        )

    @router.get("/static/vendor.js", response_model=None)
    async def vendor_js():
        js_path = Path(__file__).parent / "static" / "vendor.js"
        if js_path.exists():
            from fastapi.responses import Response

            return Response(
                content=js_path.read_bytes(), media_type="application/javascript"
            )
        return PlainTextResponse(
            content="// vendor.js not built yet",
            status_code=404,
        )

    @router.get("/static/connection-health.mjs", response_model=None)
    async def connection_health_mjs():
        mjs_path = Path(__file__).parent / "static" / "connection-health.mjs"
        if mjs_path.exists():
            from fastapi.responses import Response

            return Response(
                content=mjs_path.read_bytes(), media_type="application/javascript"
            )
        return PlainTextResponse(
            content="// connection-health.mjs not found",
            status_code=404,
        )

    # --- Status ---

    @router.get("/api/status")
    async def voice_status() -> JSONResponse:
        api_key = os.environ.get("OPENAI_API_KEY")
        vcfg = _get_voice_config()
        return JSONResponse(
            content={
                "status": "ready" if api_key else "unconfigured",
                "api_key_set": bool(api_key),
                "model": vcfg["model"],
                "voice": vcfg["voice"],
                "assistant_name": vcfg["assistant_name"],
                "turn_server": None,
            }
        )

    # --- Signaling ---

    @router.get("/session")
    async def create_session_token(
        x_api_key: str | None = Header(default=None),
    ) -> JSONResponse:
        await _require_api_key(x_api_key)

        if _is_stub_mode():
            token = _stub_voice_client_secret()
            return JSONResponse(content={"value": token})

        from voice_plugin import realtime as rt

        vcfg = _get_voice_config()
        config = rt.VoiceConfig(
            model=vcfg["model"],
            voice=vcfg["voice"],
            instructions=vcfg["instructions"],
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        )
        token = await rt.create_client_secret(config)
        return JSONResponse(content={"value": token})

    @router.post("/sdp", response_model=None)
    async def exchange_sdp(request: Request) -> PlainTextResponse | JSONResponse:
        if _is_stub_mode():
            return PlainTextResponse(
                content=_stub_voice_sdp(), media_type="application/sdp"
            )

        offer_sdp = (await request.body()).decode(errors="replace")
        if not offer_sdp:
            return JSONResponse(
                status_code=400, content={"error": "SDP offer body required"}
            )

        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                status_code=401, content={"error": "Bearer ephemeral token required"}
            )

        ephemeral_token = auth[len("Bearer "):]
        vcfg = _get_voice_config()

        from voice_plugin import realtime as rt

        sdp_answer = await rt.exchange_sdp(offer_sdp, ephemeral_token, vcfg["model"])
        return PlainTextResponse(content=sdp_answer, media_type="application/sdp")

    # --- SSE Event Stream ---

    @router.get("/events")
    async def events_stream(
        request: Request,
        origin: str | None = Header(default=None),
    ) -> StreamingResponse:
        await _check_origin(origin)

        async def _generate():
            import json as _json

            try:
                while True:
                    if await request.is_disconnected():
                        break
                    conn = _active_connection
                    if conn is not None:
                        try:
                            event = await asyncio.wait_for(
                                conn.event_queue.get(),
                                timeout=5.0,
                            )
                            if isinstance(event, tuple) and len(event) == 2:
                                raw_name, data = event
                                type_name = (
                                    str(raw_name).replace(":", "_").replace("_block", "")
                                )
                                event = {
                                    "type": type_name,
                                    "event": raw_name,
                                    **(data or {}),
                                }
                            yield f"data: {_json.dumps(event)}\n\n"
                        except TimeoutError:
                            yield ": heartbeat\n\n"
                    else:
                        yield ": heartbeat\n\n"
                        await asyncio.sleep(5.0)
            except (asyncio.CancelledError, GeneratorExit):
                pass

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # --- Session Lifecycle ---

    @router.post("/sessions")
    async def create_session(
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> JSONResponse:
        await _require_api_key(x_api_key)

        global _active_connection

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}

        workspace_root = body.get("workspace_root", str(_get_workspace_root()))

        backend = _get_backend()
        repo = _get_repo()

        conn = VoiceConnection(repository=repo, backend=backend)
        session_id = await conn.create(workspace_root)

        conv = VoiceConversation(
            id=session_id,
            title=f"Voice session {session_id[:8]}",
            status="active",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        repo.create_conversation(conv)

        _active_connection = conn

        if conn.project_id:
            repo.write_to_amplifier_transcript(session_id, conn.project_id, [])
            conv_for_meta = repo.get_conversation(session_id)
            if conv_for_meta is not None:
                repo.write_amplifier_metadata(session_id, conn.project_id, conv_for_meta)

        logger.info(
            "Voice session created: %s (project_id=%s)", session_id, conn.project_id
        )
        return JSONResponse(content={"session_id": session_id})

    @router.post("/sessions/{session_id}/resume")
    async def resume_session(
        session_id: str,
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> JSONResponse:
        await _require_api_key(x_api_key)
        _validate_session_id(session_id)

        global _active_connection

        backend = _get_backend()
        repo = _get_repo()

        session_info = await backend.get_session_info(session_id)
        if session_info is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"Session {session_id} not found or has expired"},
            )

        context = repo.get_resumption_context(session_id)

        if _is_stub_mode():
            client_secret = _stub_voice_client_secret()
        else:
            from voice_plugin import realtime as rt

            vcfg = _get_voice_config()
            config = rt.VoiceConfig(
                model=vcfg["model"],
                voice=vcfg["voice"],
                instructions=vcfg["instructions"],
                openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            )
            client_secret = await rt.create_client_secret(config)

        conn = VoiceConnection(repository=repo, backend=backend)
        conn._session_id = session_id
        conn._project_id = session_info.project_id or None
        _active_connection = conn

        await backend.resume_session(
            session_info.session_id,
            session_info.working_dir,
            event_queue=conn.event_queue,
        )

        logger.info("Voice session resumed: %s", session_id)
        return JSONResponse(
            content={
                "client_secret": client_secret,
                "context_to_inject": context,
            }
        )

    @router.post("/sessions/{session_id}/transcript")
    async def sync_transcript(
        session_id: str,
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> JSONResponse:
        await _require_api_key(x_api_key)
        _validate_session_id(session_id)

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400,
                content={"error": "Body must be a JSON object with an 'entries' key"},
            )

        entries_data: list[dict[str, Any]] = body.get("entries", [])
        repo = _get_repo()
        now = datetime.now(UTC)

        entries = [
            TranscriptEntry(
                id=str(uuid4()),
                conversation_id=session_id,
                role=e.get("role", "user"),
                content=e.get("content", ""),
                created_at=now,
                item_id=e.get("item_id"),
                tool_name=e.get("tool_name"),
                call_id=e.get("call_id"),
            )
            for e in entries_data
        ]
        repo.add_entries(session_id, entries)

        conn = _active_connection
        if conn is not None and conn.project_id:
            repo.write_to_amplifier_transcript(session_id, conn.project_id, entries)

        return JSONResponse(content={"synced": len(entries)})

    @router.post("/sessions/{session_id}/end")
    async def end_session(
        session_id: str,
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> JSONResponse:
        await _require_api_key(x_api_key)
        _validate_session_id(session_id)

        global _active_connection

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}

        _VALID_REASONS = {
            "session_limit",
            "network_error",
            "user_ended",
            "idle_timeout",
            "error",
        }
        raw_reason: str = body.get("reason", "user_ended")
        reason = raw_reason if raw_reason in _VALID_REASONS else "error"
        backend = _get_backend()
        repo = _get_repo()

        await backend.end_session(session_id)
        repo.end_conversation(session_id, reason)  # type: ignore[arg-type]
        _active_connection = None

        logger.info("Voice session ended: %s (reason=%s)", session_id, reason)
        return JSONResponse(content={"ended": True, "session_id": session_id})

    # NOTE: /sessions/stats MUST be declared before /sessions/{session_id}
    @router.get("/sessions/stats")
    async def sessions_stats(
        x_api_key: str | None = Header(default=None),
    ) -> JSONResponse:
        await _require_api_key(x_api_key)
        repo = _get_repo()
        conversations = repo.list_conversations()
        by_status: dict[str, int] = {}
        for conv in conversations:
            status = conv.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1
        return JSONResponse(
            content={
                "total": len(conversations),
                "by_status": by_status,
            }
        )

    @router.get("/sessions")
    async def list_sessions(
        x_api_key: str | None = Header(default=None),
    ) -> JSONResponse:
        await _require_api_key(x_api_key)
        repo = _get_repo()
        return JSONResponse(content=repo.list_conversations())

    # --- Tool Execution ---

    @router.post("/tools/execute")
    async def execute_tool(
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> JSONResponse:
        await _require_api_key(x_api_key)

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

        name: str = body.get("name", "")
        arguments: dict[str, Any] = body.get("arguments", {})

        if not name:
            return JSONResponse(status_code=400, content={"error": "Missing 'name' field"})

        conn = _active_connection

        if name == "delegate":
            instruction = arguments.get("instruction", "")
            if not instruction:
                return JSONResponse(
                    status_code=400, content={"error": "instruction required for delegate"}
                )
            if conn is None or conn.session_id is None:
                return JSONResponse(
                    status_code=400, content={"error": "No active voice session"}
                )
            backend = _get_backend()
            result = await backend.send_message(conn.session_id, instruction)
            return JSONResponse(content={"result": result})

        if name == "cancel_current_task":
            if conn is None:
                return JSONResponse(
                    status_code=400, content={"error": "No active voice session"}
                )
            await conn.cancel()
            return JSONResponse(content={"result": "cancelled"})

        return JSONResponse(status_code=400, content={"error": f"Unknown tool: {name}"})

    # --- Cancel ---

    @router.post("/cancel")
    async def cancel_session(
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> JSONResponse:
        await _require_api_key(x_api_key)

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

        session_id: str = body.get("session_id", "")
        level: str = body.get("level", "graceful")
        if level not in ("graceful", "immediate"):
            return JSONResponse(
                status_code=400,
                content={"error": "level must be 'graceful' or 'immediate'"},
            )

        backend = _get_backend()
        await backend.cancel_session(session_id, level=level)

        return JSONResponse(content={"cancelled": True, "session_id": session_id})

    return router
