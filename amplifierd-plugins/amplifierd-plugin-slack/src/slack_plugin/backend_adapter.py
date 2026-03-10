"""Adapter from amplifierd's SessionManager to the interface expected by the Slack bridge.

The old Slack bridge used a SessionBackend protocol. This adapter wraps the
new amplifierd SessionManager so that existing bridge code (SlackSessionManager,
events.py) can work with minimal changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """Information about a backend session."""

    session_id: str
    project_id: str = ""
    working_dir: str = ""
    is_active: bool = True
    description: str = ""


class SessionManagerAdapter:
    """Adapts amplifierd's SessionManager to the interface expected by the Slack bridge.

    Provides the subset of the old SessionBackend protocol that the bridge
    actually uses: create_session, send_message, end_session, resume_session,
    cancel_session.
    """

    def __init__(self, session_manager: Any) -> None:
        self._sm = session_manager

    async def create_session(
        self,
        working_dir: str = "~",
        bundle_name: str | None = None,
        description: str = "",
        event_queue: Any = None,  # ignored in new system
    ) -> SessionInfo:
        """Create a new Amplifier session."""
        effective_bundle = bundle_name
        if not effective_bundle:
            # Fall back to daemon's default bundle
            effective_bundle = getattr(self._sm.settings, "default_bundle", None)

        handle = await self._sm.create(
            bundle_name=effective_bundle,
            working_dir=working_dir,
        )
        return SessionInfo(
            session_id=handle.session_id,
            project_id="",
            working_dir=handle.working_dir or working_dir,
            is_active=True,
            description=description,
        )

    async def send_message(self, session_id: str, message: str) -> str:
        """Send a message to a session. Returns the response text."""
        handle = self._sm.get(session_id)
        if handle is None:
            raise ValueError(f"Session {session_id} not found")
        result = await handle.execute(message)
        return str(result) if result else ""

    async def end_session(self, session_id: str) -> None:
        """End a session and clean up."""
        await self._sm.destroy(session_id)

    async def resume_session(
        self,
        session_id: str,
        working_dir: str,
        event_queue: Any = None,
    ) -> None:
        """Resume a previously created session."""
        await self._sm.resume(session_id)

    async def cancel_session(self, session_id: str, level: str = "graceful") -> None:
        """Cancel a running session."""
        handle = self._sm.get(session_id)
        if handle is not None:
            handle.cancel(immediate=(level == "immediate"))

    def list_active_sessions(self) -> list[SessionInfo]:
        """List all active sessions."""
        sessions = self._sm.list_sessions()
        return [
            SessionInfo(
                session_id=s["session_id"],
                working_dir=s.get("working_dir", ""),
                is_active=s.get("is_active", True),
            )
            for s in sessions
            if s.get("is_active", False)
        ]
