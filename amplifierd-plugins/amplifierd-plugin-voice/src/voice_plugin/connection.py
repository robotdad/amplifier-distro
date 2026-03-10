"""VoiceConnection — manages one voice session lifecycle.

One instance per voice connection. Owns:
  - event_queue: asyncio.Queue wired to EventStreamingHook for SSE streaming
  - _hook: EventStreamingHook that maps Amplifier events to SSE wire dicts
  - _hook_unregister: Callable to unregister the hook on teardown/end

HOOK CLEANUP: Critical — without unregistering in finally, dead hook registrations
accumulate across reconnects and fire against closed queues.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from voice_plugin.protocols.event_streaming import (
    EventStreamingHook,
)

logger = logging.getLogger(__name__)

# Maximum event queue depth — bounds memory if SSE consumer is slow
_EVENT_QUEUE_MAX_SIZE = 10000


class VoiceConnection:
    """Manages one voice session lifecycle: create, teardown, end, cancel."""

    def __init__(self, repository: Any, backend: Any) -> None:
        self._repository = repository
        self._backend = backend
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=_EVENT_QUEUE_MAX_SIZE)
        self._hook: EventStreamingHook | None = None
        self._hook_unregister: Callable[[], None] | None = None
        self._session_id: str | None = None
        self._session_obj: Any = None
        self._project_id: str | None = None

    @property
    def event_queue(self) -> asyncio.Queue:
        """The asyncio.Queue used as the event bus for this connection."""
        return self._event_queue

    @property
    def session_id(self) -> str | None:
        """The current Amplifier session ID, or None if not yet created."""
        return self._session_id

    @property
    def project_id(self) -> str | None:
        """The Amplifier project ID for this session, or None if not yet created."""
        return self._project_id

    async def create(self, workspace_root: str) -> str:
        """Create an Amplifier session for this voice connection."""
        # 1. Create the streaming hook wired to our event queue
        hook = EventStreamingHook(event_queue=self._event_queue)
        self._hook = hook

        # 2. Create session via backend
        _q = self._event_queue

        def _event_forwarder(msg: dict) -> None:
            with contextlib.suppress(Exception):
                _q.put_nowait(msg)

        session = await self._backend.create_session(
            description="voice",
            working_dir=workspace_root,
            event_queue=self._event_queue,
            exclude_tools=["delegate"],
            event_forwarder=_event_forwarder,
        )

        # 3. Store session references
        self._session_obj = session
        self._session_id = session.session_id
        self._project_id = getattr(session, "project_id", None)

        # Fallback: scan filesystem for project_id
        if self._project_id is None and self._session_id is not None:
            self._project_id = self._find_project_id_from_fs(self._session_id)
            if self._project_id is None:
                logger.warning(
                    "project_id not available for voice session %s; "
                    "voice sessions will not appear in the Amplifier chat app.",
                    self._session_id,
                )

        assert self._session_id is not None

        # Fetch hook unregister callable
        get_unregister = getattr(self._backend, "get_hook_unregister", None)
        if get_unregister is not None:
            self._hook_unregister = get_unregister(self._session_id)

        return self._session_id

    async def teardown(self) -> None:
        """Handle client disconnect: mark session disconnected, always cleanup hook."""
        try:
            if self._session_id is not None:
                self._repository.update_status(self._session_id, "disconnected")
        finally:
            self._cleanup_hook()
            self._event_queue = asyncio.Queue(maxsize=_EVENT_QUEUE_MAX_SIZE)

    async def end(self, reason: str = "user_ended") -> None:
        """End the session permanently."""
        try:
            if self._session_id is not None:
                await self._backend.end_session(self._session_id)
                self._repository.end_conversation(self._session_id, reason)
        finally:
            self._cleanup_hook()

    async def cancel(self, level: str = "graceful") -> None:
        """Cancel the running session."""
        if self._session_id is not None:
            await self._backend.cancel_session(self._session_id, level=level)

    def _cleanup_hook(self) -> None:
        """Unregister the hook if one is registered. Always safe to call."""
        if self._hook_unregister is not None:
            try:
                self._hook_unregister()
            except Exception:  # noqa: BLE001
                logger.warning("Error unregistering voice event hook", exc_info=True)
            finally:
                self._hook_unregister = None

    @staticmethod
    def _find_project_id_from_fs(session_id: str) -> str | None:
        """Scan ~/.amplifier/projects/ to find the project owning this session."""
        from pathlib import Path

        projects_dir = Path.home() / ".amplifier" / "projects"
        if not projects_dir.exists():
            return None
        try:
            for project_dir in projects_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                if (project_dir / "sessions" / session_id).exists():
                    return project_dir.name
        except OSError:
            logger.debug("Error scanning projects dir for project_id", exc_info=True)
        return None
