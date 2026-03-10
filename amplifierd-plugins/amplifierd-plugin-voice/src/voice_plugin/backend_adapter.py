"""Backend adapter for the voice plugin.

Wraps amplifierd's SessionManager to provide the interface expected by
VoiceConnection and the voice route handlers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """Lightweight session info returned by get_session_info."""

    session_id: str
    project_id: str
    working_dir: str


class VoiceBackendAdapter:
    """Wraps amplifierd SessionManager for the voice plugin.

    Provides the same method signatures that VoiceConnection and the
    voice routes call on the old SessionBackend.
    """

    def __init__(self, session_manager: Any) -> None:
        self._sm = session_manager

    async def create_session(
        self,
        description: str = "",
        working_dir: str = "~",
        event_queue: Any = None,
        exclude_tools: list[str] | None = None,
        event_forwarder: Any = None,
    ) -> Any:
        """Create an Amplifier session.

        Returns the SessionHandle which has .session_id, .working_dir, etc.
        The event_queue, exclude_tools, and event_forwarder parameters are
        voice-specific — they are forwarded to the session manager if supported.
        """
        kwargs: dict[str, Any] = {"working_dir": working_dir}

        # Use the default bundle from daemon settings
        default_bundle = getattr(
            getattr(self._sm, "_settings", None), "default_bundle", None
        )
        if default_bundle:
            kwargs["bundle_name"] = default_bundle

        handle = await self._sm.create(**kwargs)
        return handle

    async def send_message(self, session_id: str, message: str) -> str:
        """Send a message to a session (used for delegate tool)."""
        handle = self._sm.get(session_id)
        if handle is None:
            raise ValueError(f"Session {session_id} not found")
        result = await handle.execute(message)
        return str(result) if result else ""

    async def end_session(self, session_id: str) -> None:
        """End/destroy a session."""
        await self._sm.destroy(session_id)

    async def cancel_session(self, session_id: str, level: str = "graceful") -> None:
        """Cancel a running session."""
        handle = self._sm.get(session_id)
        if handle is not None:
            handle.cancel(immediate=(level == "immediate"))

    async def get_session_info(self, session_id: str) -> SessionInfo | None:
        """Get session info for resume."""
        handle = self._sm.get(session_id)
        if handle is None:
            return None
        return SessionInfo(
            session_id=handle.session_id,
            project_id=getattr(handle, "project_id", "") or "",
            working_dir=handle.working_dir or "~",
        )

    async def resume_session(
        self,
        session_id: str,
        working_dir: str | None = None,
        event_queue: Any = None,
    ) -> None:
        """Resume a session."""
        await self._sm.resume(session_id)

    def get_hook_unregister(self, session_id: str) -> Any:
        """Get a hook unregistration callable (if supported)."""
        return None
