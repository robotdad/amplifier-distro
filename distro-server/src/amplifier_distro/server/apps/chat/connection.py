"""ChatConnection — manages one WebSocket session lifecycle.

One instance per WebSocket connection. Owns:
  - _auth_handshake(): validate token if api_key is configured
  - _receive_loop(): read client messages, dispatch to backend
  - _event_fanout_loop(): drain asyncio.Queue, translate, send to WS
  - event_queue: asyncio.Queue wired to FoundationBackend.on_stream
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from starlette.websockets import WebSocketDisconnect

from amplifier_distro.server.apps.chat.translator import SessionEventTranslator

if TYPE_CHECKING:
    from fastapi import WebSocket

    from amplifier_distro.server.session_backend import FoundationBackend

logger = logging.getLogger(__name__)

_AUTH_TIMEOUT_S = 30.0

# Application-level heartbeat interval (seconds). Keeps the WebSocket
# alive through reverse proxies that track application-layer data frames
# rather than transport-level pings. Without this, proxies with idle
# timeouts (nginx: 75s, ALB: 60s, Cloudflare: 100s) may kill the
# connection during long tool executions that produce no events.
_HEARTBEAT_INTERVAL_S = 15.0

# Maximum event queue depth — bounds memory if the WebSocket consumer is slow
_EVENT_QUEUE_MAX_SIZE = 10000

# Sentinel: put into event_queue to stop _event_fanout_loop
_STOP: object = object()

# Session IDs: alphanumeric, hyphens, underscores only (path traversal prevention)
_VALID_SESSION_ID = re.compile(r"^[a-zA-Z0-9_\-]+$")

# Module-level registry of all active WebSocket connections.
_active_connections: set[ChatConnection] = set()


async def broadcast_to_all(message: dict) -> None:
    """Send a JSON message to every connected chat WebSocket client."""
    import json as _json

    payload = _json.dumps(message)
    for conn in list(_active_connections):
        with contextlib.suppress(Exception):
            await conn._ws.send_text(payload)


class ChatConnection:
    """Manages one WebSocket connection: auth, receive loop, event fanout."""

    def __init__(
        self,
        ws: WebSocket,
        backend: FoundationBackend,
        config: Any,
    ) -> None:
        self._ws = ws
        self._backend = backend
        self._config = config
        self.event_queue: asyncio.Queue = asyncio.Queue(maxsize=_EVENT_QUEUE_MAX_SIZE)
        self._translator = SessionEventTranslator()
        self._session_id: str | None = None
        # keeps strong refs to fire-and-forget tasks so GC can't collect them
        self._tasks: set[asyncio.Task] = set()
        self._active_execution: asyncio.Task | None = None
        # Hook unregister callable -- mirrors VoiceConnection._hook_unregister.
        # Called on disconnect to remove stale hooks from the coordinator and
        # clear _wired_sessions so the next reconnect re-registers fresh hooks.
        self._hook_unregister: Callable[[], None] | None = None
        # tracks which local block indices received at least one delta
        self._seen_deltas: set[int] = set()

    async def run(self) -> None:
        """Full connection lifecycle: auth then concurrent receive + fanout."""
        await self._ws.accept()
        try:
            await self._auth_handshake()
        except WebSocketDisconnect:
            return

        fanout_task = asyncio.create_task(
            self._event_fanout_loop(), name="event_fanout_loop"
        )
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="heartbeat")
        _active_connections.add(self)
        try:
            await self._receive_loop()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            logger.warning("ChatConnection receive error", exc_info=True)
        finally:
            _active_connections.discard(self)
            # Stop heartbeat first (it sends to WS which may be dead)
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            # Cancel non-execution tasks only. The active execution is
            # intentionally left running server-side so it can finish and
            # persist its transcript. When the user reconnects, the event
            # queue will be re-wired and events from the still-running (or
            # completed) execution will flow to the new connection.
            # This mirrors VoiceConnection.teardown() which does NOT cancel
            # execution on disconnect.
            for task in list(self._tasks):
                if task is not self._active_execution:
                    task.cancel()
            non_exec = [t for t in self._tasks if t is not self._active_execution]
            if non_exec:
                await asyncio.gather(*non_exec, return_exceptions=True)
            # Stop the fanout loop gracefully via sentinel first, then
            # force-cancel as fallback. Using put_nowait avoids blocking
            # if the queue is full (which happens when the fanout died
            # mid-execution and events piled up).
            with contextlib.suppress(asyncio.QueueFull):
                self.event_queue.put_nowait(_STOP)
            try:
                await asyncio.wait_for(asyncio.shield(fanout_task), timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                fanout_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await fanout_task
            # Do NOT call _cleanup_hook() here. Hooks are intentionally left
            # registered so the still-running execution's events can be
            # redirected to the new queue via QueueHolder swap on reconnect.
            # _cleanup_hook() is still called from _dispatch_command (bundle/cwd)
            # for explicit session replacement.

    def _cleanup_hook(self) -> None:
        """Unregister the hook if one is registered. Always safe to call.

        Mirrors VoiceConnection._cleanup_hook() -- removes stale hooks from
        the coordinator and clears _wired_sessions so the next reconnect
        re-registers fresh hooks against the new event queue.
        """
        if self._hook_unregister is not None:
            try:
                self._hook_unregister()
            except Exception:  # noqa: BLE001
                logger.warning("Error unregistering chat event hooks", exc_info=True)
            finally:
                self._hook_unregister = None

    def _fetch_hook_unregister(self, session_id: str) -> None:
        """Fetch and store the hook unregister callable for a session.

        Called after create_session/resume_session to capture the callable
        that _cleanup_hook() will invoke on disconnect.
        """
        get_unregister = getattr(self._backend, "get_hook_unregister", None)
        if get_unregister is not None:
            self._hook_unregister = get_unregister(session_id)

    async def _heartbeat_loop(self) -> None:
        """Send periodic application-level heartbeats.

        Keeps the WebSocket alive through proxies that may ignore
        protocol-level WebSocket PINGs and track only application data.
        Runs as a background task alongside the receive and fanout loops.
        """
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
                try:
                    await self._ws.send_json({"type": "heartbeat"})
                except WebSocketDisconnect:
                    break
                except Exception:  # noqa: BLE001
                    break
        except asyncio.CancelledError:
            pass

    async def _auth_handshake(self) -> None:
        """Validate auth token if api_key is configured.

        Also validates WebSocket Origin header to prevent CSRF attacks —
        any browser page can open a WebSocket connection, bypassing SOP.
        Only allows connections from localhost origins.
        """
        # Origin check — prevents CSRF from evil.com → localhost:PORT
        # When the user binds to a non-localhost host (e.g. 0.0.0.0, a LAN IP,
        # a Tailscale IP), they've explicitly opted into network access.
        # The API key is the real security boundary — CSRF origin checks only
        # protect against ambient credentials (cookies), which we don't use.
        server_host = getattr(self._config.server, "host", "127.0.0.1")
        origin = self._ws.headers.get("origin", "")
        if origin:  # skip for non-browser clients (no Origin header)
            if server_host not in ("127.0.0.1", "localhost"):
                pass  # non-localhost host: skip origin restriction
            else:
                allowed = {"http://localhost", "http://127.0.0.1", "https://localhost"}
                is_localhost = any(
                    origin.startswith(allowed_prefix) for allowed_prefix in allowed
                )
                if not is_localhost:
                    logger.warning(
                        "Rejected WebSocket from non-localhost origin: %s", origin
                    )
                    await self._ws.close(4003, "Forbidden origin")
                    raise WebSocketDisconnect(code=4003)

        api_key = getattr(self._config.server, "api_key", None)
        if api_key is None:
            return

        try:
            msg = await asyncio.wait_for(
                self._ws.receive_json(), timeout=_AUTH_TIMEOUT_S
            )
        except TimeoutError:
            logger.warning("Auth handshake timed out — closing connection")
            await self._ws.close(4008, "Auth timeout")
            raise WebSocketDisconnect(code=4008) from None

        token = msg.get("token", "")
        if msg.get("type") != "auth" or not hmac.compare_digest(
            str(token), str(api_key)
        ):
            await self._ws.close(4001, "Unauthorized")
            raise WebSocketDisconnect(code=4001)  # signal run() to exit immediately

        await self._ws.send_json({"type": "auth_ok"})

    async def _receive_loop(self) -> None:
        """Read messages from client and dispatch by type.

        Raises WebSocketDisconnect when the client disconnects — callers
        (e.g. run()) are responsible for handling it.
        """
        while True:
            msg = await self._ws.receive_json()  # propagates WebSocketDisconnect

            msg_type = msg.get("type", "")
            try:
                await self._dispatch(msg_type, msg)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Error dispatching message type=%s", msg_type, exc_info=True
                )
                await self._ws.send_json(
                    {
                        "type": "execution_error",
                        "error": "Internal error processing message",
                    }
                )

    async def _dispatch(self, msg_type: str, msg: dict[str, Any]) -> None:
        """Route a received message to the appropriate handler."""
        match msg_type:
            case "create_session":
                await self._handle_create_session(msg)

            case "prompt":
                content = msg.get("content", "")
                images = msg.get("images")
                if self._active_execution and not self._active_execution.done():
                    await self._ws.send_json(
                        {
                            "type": "execution_error",
                            "error": "Execution in progress. Send cancel first.",
                        }
                    )
                    return
                task = asyncio.create_task(
                    self._execute(content, images), name=f"execute-{self._session_id}"
                )
                self._active_execution = task
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

            case "cancel":
                level = msg.get("level", "graceful")
                if self._session_id:
                    await self._backend.cancel_session(self._session_id, level)

            case "approval_response":
                req_id = msg.get("id", "")
                choice = msg.get("choice", "deny")
                if self._session_id:
                    self._backend.resolve_approval(self._session_id, req_id, choice)

            case "command":
                name = msg.get("name", "")
                args = msg.get("args", [])
                await self._handle_command(name, args)

            case "ping":
                await self._ws.send_json({"type": "pong"})

            case _:
                logger.debug("Unknown message type: %s", msg_type)

    async def _send_json(self, data: dict[str, Any]) -> None:
        """Send a JSON message to the WebSocket client."""
        await self._ws.send_json(data)

    async def _handle_create_session(self, msg: dict[str, Any]) -> None:
        """Create or resume an Amplifier session."""
        # TODO: load saved preferences as fallback defaults for cwd, bundle, behaviors
        # from amplifier_distro.server.apps.chat.preferences import load_preferences
        cwd = msg.get("cwd", "~")
        bundle = msg.get("bundle")
        resume_session_id = msg.get("resume_session_id")

        # --- Input validation (path traversal / injection prevention) ---
        if resume_session_id and not _VALID_SESSION_ID.match(str(resume_session_id)):
            await self._send_json(
                {"type": "error", "error": "Invalid session ID format"}
            )
            return

        if "\x00" in cwd:
            await self._send_json(
                {"type": "error", "error": "Invalid working directory"}
            )
            return

        try:
            if resume_session_id:
                await self._backend.resume_session(
                    str(resume_session_id),
                    cwd,
                    event_queue=self.event_queue,
                )
                info = await self._backend.get_session_info(str(resume_session_id))
                session_id = (
                    info.session_id if info is not None else str(resume_session_id)
                )
                session_cwd = (
                    str(info.working_dir)
                    if info is not None and info.working_dir
                    else str(cwd)
                )
            else:
                info = await self._backend.create_session(
                    working_dir=cwd,
                    bundle_name=bundle,
                    event_queue=self.event_queue,
                )
                session_id = info.session_id
                session_cwd = str(info.working_dir)

            self._session_id = session_id
            self._translator.reset()

            # Fetch and store the hook unregister callable so _cleanup_hook()
            # can remove registered hooks on disconnect (mirrors VoiceConnection).
            self._fetch_hook_unregister(session_id)

            await self._ws.send_json(
                {
                    "type": "session_created",
                    "session_id": session_id,
                    "cwd": session_cwd,
                    "bundle": bundle,
                }
            )
        except Exception:  # noqa: BLE001
            logger.warning("Session creation failed", exc_info=True)
            await self._ws.send_json(
                {
                    "type": "execution_error",
                    "error": "Session creation failed. Check server logs.",
                }
            )

    async def _execute(self, content: str, images: list[str] | None = None) -> None:
        """Execute a prompt — events stream via event_queue."""
        if not self._session_id:
            await self._ws.send_json(
                {
                    "type": "execution_error",
                    "error": "No session. Send create_session first.",
                }
            )
            return

        try:
            await self._backend.execute(self._session_id, content, images)
        except Exception:  # noqa: BLE001
            logger.warning("Execution error", exc_info=True)
            with contextlib.suppress(Exception):
                await self._ws.send_json(
                    {
                        "type": "execution_error",
                        "error": "Execution failed. Check server logs.",
                    }
                )

    async def _handle_command(self, name: str, args: list[str]) -> None:
        """Handle a slash command from the client."""
        try:
            result = await self._dispatch_command(name, args)
            await self._ws.send_json(
                {"type": "command_result", "command": name, "result": result}
            )
        except Exception:  # noqa: BLE001
            logger.warning("Command '%s' failed", name, exc_info=True)
            await self._ws.send_json(
                {
                    "type": "command_result",
                    "command": name,
                    "result": {"error": f"Command '{name}' failed. Check server logs."},
                }
            )

    async def _dispatch_command(self, name: str, args: list[str]) -> dict[str, Any]:
        """Route server-side slash commands."""
        match name:
            case "status":
                return {
                    "session_id": self._session_id,
                    "status": "active" if self._session_id else "no_session",
                }
            case "bundle" if args:
                new_bundle = args[0]
                if self._session_id:
                    self._cleanup_hook()
                    await self._backend.cancel_session(self._session_id, "graceful")
                    with contextlib.suppress(Exception):
                        await self._backend.end_session(self._session_id)
                info = await self._backend.create_session(
                    bundle_name=new_bundle,
                    event_queue=self.event_queue,
                )
                self._session_id = info.session_id
                self._translator.reset()
                self._fetch_hook_unregister(info.session_id)
                return {"bundle": new_bundle, "session_id": info.session_id}
            case "cwd" if args:
                new_cwd = args[0]
                if "\x00" in new_cwd:
                    return {"error": "Invalid working directory"}
                if self._session_id:
                    self._cleanup_hook()
                    await self._backend.cancel_session(self._session_id, "graceful")
                    with contextlib.suppress(Exception):
                        await self._backend.end_session(self._session_id)
                info = await self._backend.create_session(
                    working_dir=new_cwd,
                    event_queue=self.event_queue,
                )
                self._session_id = info.session_id
                self._translator.reset()
                self._fetch_hook_unregister(info.session_id)
                return {"cwd": str(info.working_dir), "session_id": info.session_id}
            case "config":
                return self._build_config_summary()
            case "tools":
                return self._build_tools_list()
            case "agents":
                return self._build_agents_list()
            case "modes":
                return self._build_modes_list()
            case "mode":
                return self._handle_mode_command(args)
            case _:
                return {"error": f"Unknown command: {name}"}

    def _build_config_summary(self) -> dict[str, Any]:
        """Build a structured config summary for the /config command."""
        if not self._session_id:
            return {"error": "No active session"}

        config = self._backend.get_session_config(self._session_id)
        if config is None:
            return {"error": "Session config unavailable"}

        session = config.get("session", {})
        providers = config.get("providers", [])
        tools = config.get("tools", [])
        hooks = config.get("hooks", [])
        agents = config.get("agents", {})

        # Extract provider summaries
        provider_list = []
        for p in providers:
            entry: dict[str, Any] = {"module": p.get("module", "unknown")}
            pc = p.get("config", {})
            if pc.get("model"):
                entry["model"] = pc["model"]
            if pc.get("priority") is not None:
                entry["priority"] = pc["priority"]
            provider_list.append(entry)

        # Extract tool names
        tool_names = [t.get("module", "unknown") for t in tools]

        # Extract hook names
        hook_names = [h.get("module", "unknown") for h in hooks]

        # Extract agent names (filter structural keys)
        agent_names = (
            sorted(k for k in agents if k not in ("dirs", "include", "inline"))
            if isinstance(agents, dict)
            else []
        )

        # Orchestrator / context
        orch = session.get("orchestrator", "unknown")
        if isinstance(orch, dict):
            orch = orch.get("module", "unknown")
        ctx = session.get("context", "unknown")
        if isinstance(ctx, dict):
            ctx = ctx.get("module", "unknown")

        return {
            "type": "config",
            "session": {"orchestrator": orch, "context": ctx},
            "providers": provider_list,
            "tools": tool_names,
            "hooks": hook_names,
            "agents": agent_names,
        }

    def _build_tools_list(self) -> dict[str, Any]:
        """Build a structured tool list for the /tools command."""
        if not self._session_id:
            return {"error": "No active session"}
        tools = self._backend.list_tools(self._session_id)
        if tools is None:
            return {"error": "Session unavailable"}
        return {"type": "tools", "tools": tools}

    def _build_agents_list(self) -> dict[str, Any]:
        """Build a structured agent list for the /agents command."""
        if not self._session_id:
            return {"error": "No active session"}
        config = self._backend.get_session_config(self._session_id)
        if config is None:
            return {"error": "Session config unavailable"}
        agents = config.get("agents", {})
        agent_list = []
        if isinstance(agents, dict):
            for name, spec in sorted(agents.items()):
                if name in ("dirs", "include", "inline") or not isinstance(spec, dict):
                    continue
                agent_list.append(
                    {
                        "name": name,
                        "description": spec.get("description", "No description"),
                    }
                )
        return {"type": "agents", "agents": agent_list}

    def _build_modes_list(self) -> dict[str, Any]:
        """Build a structured modes list for the /modes command."""
        if not self._session_id:
            return {"error": "No active session"}
        result = self._backend.list_modes(self._session_id)
        if result is None:
            return {"error": "Session unavailable"}
        return {"type": "modes", **result}

    def _handle_mode_command(self, args: list[str]) -> dict[str, Any]:
        """Handle /mode [name] [on|off] command."""
        if not self._session_id:
            return {"error": "No active session"}

        if not args:
            # /mode with no args: show current mode
            result = self._backend.list_modes(self._session_id)
            if result is None:
                return {"error": "Session unavailable"}
            active = result.get("active_mode")
            if active:
                return {"type": "mode", "active_mode": active}
            return {"type": "mode", "active_mode": None, "message": "No mode active"}

        mode_arg = args[0]

        # /mode off -> deactivate
        if mode_arg == "off":
            return {"type": "mode", **self._backend.set_mode(self._session_id, None)}

        # /mode <name> [on|off]
        qualifier = args[1] if len(args) > 1 else None
        if qualifier == "off":
            # /mode <name> off -> deactivate only if it's the active mode
            result = self._backend.list_modes(self._session_id)
            current = result.get("active_mode") if result else None
            if current == mode_arg:
                return {
                    "type": "mode",
                    **self._backend.set_mode(self._session_id, None),
                }
            return {"type": "mode", "active_mode": current, "message": "Not active"}
        if qualifier == "on":
            # /mode <name> on -> force activate
            return {
                "type": "mode",
                **self._backend.set_mode(self._session_id, mode_arg),
            }
        # /mode <name> -> toggle
        result = self._backend.list_modes(self._session_id)
        current = result.get("active_mode") if result else None
        if current == mode_arg:
            return {"type": "mode", **self._backend.set_mode(self._session_id, None)}
        return {"type": "mode", **self._backend.set_mode(self._session_id, mode_arg)}

    async def _event_fanout_loop(self) -> None:
        """Drain event_queue and forward translated events to WebSocket.

        Stops on _STOP sentinel. Synthesizes streaming deltas for non-streaming
        providers that deliver full text in content_end with no prior deltas.
        """

        _server_index = SessionEventTranslator.server_index
        _block_text = SessionEventTranslator.block_text

        while True:
            raw = await self.event_queue.get()
            if raw is _STOP:
                break
            event_name, data = raw
            try:
                # Track which local indices received non-empty deltas.
                # Only mark as streamed when actual content was extracted —
                # dict-format deltas (Anthropic native) previously fell through
                # to "" and would wrongly suppress the synthetic streaming fallback.
                if event_name == "content_block:delta":
                    raw_delta = data.get("delta", "")
                    if isinstance(raw_delta, dict):
                        raw_delta = (
                            raw_delta.get("text") or raw_delta.get("thinking") or ""
                        )
                    if raw_delta and isinstance(raw_delta, str):
                        local_idx = self._translator.get_local_index(
                            _server_index(data)
                        )
                        self._seen_deltas.add(local_idx)

                # Synthetic streaming: if content_end has text but no deltas were seen,
                # synthesize chunked deltas to animate the response
                if event_name == "content_block:end":
                    local_idx = self._translator.get_local_index(_server_index(data))
                    text = _block_text(data)
                    if text and local_idx not in self._seen_deltas:
                        # Synthesize: send chunked deltas before the end event
                        chunk_size = 12
                        server_index = _server_index(data)
                        for i in range(0, len(text), chunk_size):
                            chunk = text[i : i + chunk_size]
                            delta_msg = self._translator.translate(
                                "content_block:delta",
                                {"delta": chunk, "block_index": server_index},
                            )
                            if delta_msg is not None:
                                await self._ws.send_json(delta_msg)
                    self._seen_deltas.discard(local_idx)

                msg = self._translator.translate(event_name, data)
                if msg is not None:
                    await self._ws.send_json(msg)

                # Reset seen_deltas on prompt_complete
                if event_name == "orchestrator:complete":
                    self._seen_deltas.clear()

            except WebSocketDisconnect:
                logger.debug("WebSocket disconnected during event fanout")
                break
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Error translating/sending event %s session=%s",
                    event_name,
                    self._session_id,
                    exc_info=True,
                )
