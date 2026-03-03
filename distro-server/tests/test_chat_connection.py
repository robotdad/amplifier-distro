"""Tests for ChatConnection."""

from __future__ import annotations

import asyncio

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_distro.server.apps.chat.connection import _STOP


def make_ws(messages: list[dict], headers: dict[str, str] | None = None):
	    """Create a mock WebSocket that replays messages then raises disconnect."""
	    from starlette.websockets import WebSocketDisconnect

	    ws = MagicMock()
	    ws.accept = AsyncMock()
	    ws.close = AsyncMock()
	    ws.send_json = AsyncMock()
	    ws.headers = headers or {}

	    msg_iter = iter(messages)

	    async def receive_json():
	        try:
	            return next(msg_iter)
	        except StopIteration:
	            raise WebSocketDisconnect(code=1000) from None

	    ws.receive_json = receive_json
	    return ws


def make_backend(session_id: str = "test-sess-001"):
	    backend = MagicMock()
	    info = MagicMock()
	    info.session_id = session_id
	    info.working_dir = "/tmp/test"
	    backend.create_session = AsyncMock(return_value=info)
	    backend.resume_session = AsyncMock(return_value=None)
	    backend.get_session_info = AsyncMock(return_value=info)
	    backend.execute = AsyncMock(return_value=None)
	    backend.cancel_session = AsyncMock(return_value=None)
	    backend.resolve_approval = MagicMock(return_value=True)
	    return backend


def make_config(api_key: str | None = None, host: str = "127.0.0.1"):
	    config = MagicMock()
	    config.server = MagicMock()
	    config.server.api_key = api_key
	    config.server.host = host
	    return config


class TestAuthHandshake:
	    @pytest.mark.asyncio
	    async def test_no_api_key_skips_auth(self):
	        """When api_key is None, auth is skipped immediately."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([])
	        backend = make_backend()
	        config = make_config(api_key=None)

	        conn = ChatConnection(ws, backend, config)
	        await conn._auth_handshake()
	        ws.close.assert_not_called()

	    @pytest.mark.asyncio
	    async def test_correct_token_sends_auth_ok(self):
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([{"type": "auth", "token": "secret"}])
	        backend = make_backend()
	        config = make_config(api_key="secret")

	        conn = ChatConnection(ws, backend, config)
	        await conn._auth_handshake()

	        ws.send_json.assert_awaited_once_with({"type": "auth_ok"})
	        ws.close.assert_not_called()

	    @pytest.mark.asyncio
	    async def test_wrong_token_closes_4001(self):
	        from starlette.websockets import WebSocketDisconnect

	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([{"type": "auth", "token": "wrong"}])
	        backend = make_backend()
	        config = make_config(api_key="secret")

	        conn = ChatConnection(ws, backend, config)
	        with pytest.raises(WebSocketDisconnect):
	            await conn._auth_handshake()

	        ws.close.assert_awaited_once_with(4001, "Unauthorized")


class TestReceiveLoop:
	    @pytest.mark.asyncio
	    async def test_create_session_message(self):
	        from starlette.websockets import WebSocketDisconnect

	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws(
	            [
	                {
	                    "type": "create_session",
	                    "bundle": "foundation",
	                    "cwd": "/tmp",
	                    "behaviors": [],
	                },
	            ]
	        )
	        backend = make_backend("sess-abc")
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        with pytest.raises(WebSocketDisconnect):
	            await conn._receive_loop()

	        backend.create_session.assert_awaited_once()
	        call_kwargs = backend.create_session.call_args.kwargs
	        assert call_kwargs.get("working_dir") == "/tmp"

	    @pytest.mark.asyncio
	    async def test_ping_sends_pong(self):
	        from starlette.websockets import WebSocketDisconnect

	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([{"type": "ping"}])
	        backend = make_backend()
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        with pytest.raises(WebSocketDisconnect):
	            await conn._receive_loop()

	        sent = [call.args[0] for call in ws.send_json.await_args_list]
	        assert any(m.get("type") == "pong" for m in sent)

	    @pytest.mark.asyncio
	    async def test_create_session_with_resume_id_calls_backend_resume(self):
	        from starlette.websockets import WebSocketDisconnect

	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws(
	            [
	                {
	                    "type": "create_session",
	                    "cwd": "/tmp/resume",
	                    "resume_session_id": "sess-resume-123",
	                },
	            ]
	        )
	        backend = make_backend("sess-resume-123")
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        with pytest.raises(WebSocketDisconnect):
	            await conn._receive_loop()

	        backend.resume_session.assert_awaited_once_with(
	            "sess-resume-123",
	            "/tmp/resume",
	            event_queue=conn.event_queue,
	        )
	        backend.create_session.assert_not_awaited()

	        sent = [call.args[0] for call in ws.send_json.await_args_list]
	        created = [m for m in sent if m.get("type") == "session_created"]
	        assert len(created) == 1
	        assert created[0]["session_id"] == "sess-resume-123"


class TestEventFanout:
	    @pytest.mark.asyncio
	    async def test_events_forwarded_to_websocket(self):
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([])
	        backend = make_backend()
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        await conn.event_queue.put(("orchestrator:complete", {"turn_count": 1}))
	        await conn.event_queue.put(_STOP)  # sentinel to stop the loop

	        await conn._event_fanout_loop()

	        sent = [call.args[0] for call in ws.send_json.await_args_list]
	        assert any(m.get("type") == "prompt_complete" for m in sent)

	    @pytest.mark.asyncio
	    async def test_unknown_events_not_forwarded(self):
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([])
	        backend = make_backend()
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        await conn.event_queue.put(("some:unknown:event", {}))
	        await conn.event_queue.put(_STOP)

	        await conn._event_fanout_loop()

	        # Unknown event produces None from translator — nothing sent
	        ws.send_json.assert_not_awaited()


class TestInputValidation:
	    """Validate that untrusted WebSocket inputs are sanitized."""

	    @pytest.mark.asyncio
	    async def test_resume_rejects_path_traversal_session_id(self):
	        """A session ID with path traversal characters should be rejected."""
	        from starlette.websockets import WebSocketDisconnect

	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws(
	            [
	                {
	                    "type": "create_session",
	                    "cwd": "/tmp",
	                    "resume_session_id": "../../../etc/passwd",
	                },
	            ]
	        )
	        backend = make_backend()
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        with pytest.raises(WebSocketDisconnect):
	            await conn._receive_loop()

	        # Backend should NOT have been called
	        backend.resume_session.assert_not_awaited()
	        backend.create_session.assert_not_awaited()

	        sent = [call.args[0] for call in ws.send_json.await_args_list]
	        errors = [m for m in sent if m.get("type") == "error"]
	        assert len(errors) == 1
	        assert "Invalid session ID" in errors[0]["error"]

	    @pytest.mark.asyncio
	    async def test_resume_rejects_session_id_with_spaces(self):
	        """Session IDs with spaces should be rejected."""
	        from starlette.websockets import WebSocketDisconnect

	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws(
	            [
	                {
	                    "type": "create_session",
	                    "cwd": "/tmp",
	                    "resume_session_id": "bad session id",
	                },
	            ]
	        )
	        backend = make_backend()
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        with pytest.raises(WebSocketDisconnect):
	            await conn._receive_loop()

	        backend.resume_session.assert_not_awaited()

	    @pytest.mark.asyncio
	    async def test_resume_accepts_valid_session_id(self):
	        """Valid session IDs (alphanumeric, hyphens, underscores) should pass."""
	        from starlette.websockets import WebSocketDisconnect

	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws(
	            [
	                {
	                    "type": "create_session",
	                    "cwd": "/tmp",
	                    "resume_session_id": "sess_abc-123_DEF",
	                },
	            ]
	        )
	        backend = make_backend("sess_abc-123_DEF")
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        with pytest.raises(WebSocketDisconnect):
	            await conn._receive_loop()

	        # Valid ID should reach the backend
	        backend.resume_session.assert_awaited_once()

	    @pytest.mark.asyncio
	    async def test_cwd_rejects_null_bytes(self):
	        """Working directory with null bytes should be rejected."""
	        from starlette.websockets import WebSocketDisconnect

	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws(
	            [
	                {
	                    "type": "create_session",
	                    "cwd": "/tmp/\x00evil",
	                },
	            ]
	        )
	        backend = make_backend()
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        with pytest.raises(WebSocketDisconnect):
	            await conn._receive_loop()

	        backend.create_session.assert_not_awaited()

	        sent = [call.args[0] for call in ws.send_json.await_args_list]
	        errors = [m for m in sent if m.get("type") == "error"]
	        assert len(errors) == 1
	        assert "Invalid working directory" in errors[0]["error"]


class TestHookCleanup:
	    """Tests for hook unregistration on disconnect (BUG-1, issue #59)."""

	    @pytest.mark.asyncio
	    async def test_hook_unregister_not_called_on_disconnect(self):
	        """ChatConnection must NOT call hook_unregister on plain disconnect."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        unregister_called = False

	        def mock_unregister():
	            nonlocal unregister_called
	            unregister_called = True

	        ws = make_ws(
	            [
	                {"type": "create_session", "cwd": "/tmp"},
	            ]
	        )
	        backend = make_backend()
	        backend.get_hook_unregister = MagicMock(return_value=mock_unregister)
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        await conn.run()

	        assert not unregister_called, "hook_unregister must NOT be called on plain disconnect"

	    @pytest.mark.asyncio
	    async def test_hook_unregister_fetched_on_create_session(self):
	        """ChatConnection must fetch hook_unregister after creating a session."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws(
	            [
	                {"type": "create_session", "cwd": "/tmp"},
	            ]
	        )
	        backend = make_backend("sess-new")
	        backend.get_hook_unregister = MagicMock(return_value=lambda: None)
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        await conn.run()

	        backend.get_hook_unregister.assert_called_once_with("sess-new")

	    @pytest.mark.asyncio
	    async def test_hook_unregister_fetched_on_resume_session(self):
	        """ChatConnection must fetch hook_unregister after resuming a session."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws(
	            [
	                {
	                    "type": "create_session",
	                    "cwd": "/tmp",
	                    "resume_session_id": "sess-resumed",
	                },
	            ]
	        )
	        backend = make_backend("sess-resumed")
	        backend.get_hook_unregister = MagicMock(return_value=lambda: None)
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        await conn.run()

	        backend.get_hook_unregister.assert_called_once_with("sess-resumed")

	    @pytest.mark.asyncio
	    async def test_hook_unregister_cleared_after_call(self):
	        """After fetching hook_unregister, the reference should be kept (not cleared on disconnect)."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws(
	            [
	                {"type": "create_session", "cwd": "/tmp"},
	            ]
	        )
	        backend = make_backend()
	        backend.get_hook_unregister = MagicMock(return_value=lambda: None)
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        await conn.run()

	        assert conn._hook_unregister is not None, (
	            "hook_unregister reference must be kept since cleanup doesn't happen on disconnect"
	        )

	    @pytest.mark.asyncio
	    async def test_hook_unregister_error_does_not_crash(self):
	        """If hook_unregister raises during /bundle command, it should not crash."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        def bad_unregister():
	            raise RuntimeError("unregister failed")

	        ws = make_ws(
	            [
	                {"type": "create_session", "cwd": "/tmp"},
	                {"type": "command", "name": "bundle", "args": ["new-bundle"]},
	            ]
	        )
	        backend = make_backend("sess-old")
	        backend.get_hook_unregister = MagicMock(return_value=bad_unregister)
	        backend.end_session = AsyncMock()
	        # create_session is called twice: once for initial, once for bundle command
	        new_info = MagicMock()
	        new_info.session_id = "sess-new"
	        new_info.working_dir = "/tmp"
	        old_info = MagicMock()
	        old_info.session_id = "sess-old"
	        old_info.working_dir = "/tmp"
	        backend.create_session = AsyncMock(side_effect=[old_info, new_info])
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        # Should not raise despite the bad unregister during bundle command
	        await conn.run()

	        # Reference should still be set to the new unregister even after error
	        assert conn._hook_unregister is not None

	    @pytest.mark.asyncio
	    async def test_no_hook_unregister_when_no_session(self):
	        """When no session is created, no hook_unregister should be called."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([])  # disconnect immediately, no create_session
	        backend = make_backend()
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        await conn.run()

	        assert conn._hook_unregister is None

	    @pytest.mark.asyncio
	    async def test_bundle_command_cleans_old_hooks_and_wires_new(self):
	        """Switching bundle via /command must cleanup old hooks and wire new ones."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        calls = []

	        def old_unregister():
	            calls.append("old_unregister")

	        def new_unregister():
	            calls.append("new_unregister")

	        ws = make_ws(
	            [
	                {"type": "create_session", "cwd": "/tmp"},
	                {"type": "command", "name": "bundle", "args": ["new-bundle"]},
	            ]
	        )
	        backend = make_backend("sess-old")
	        # First call returns old_unregister, second returns new_unregister
	        backend.get_hook_unregister = MagicMock(
	            side_effect=[old_unregister, new_unregister]
	        )
	        backend.end_session = AsyncMock()
	        # create_session returns different session IDs
	        new_info = MagicMock()
	        new_info.session_id = "sess-new"
	        new_info.working_dir = "/tmp"
	        backend.create_session = AsyncMock(side_effect=[backend.create_session.return_value, new_info])
	        # Fix: re-set the first return to the original
	        old_info = MagicMock()
	        old_info.session_id = "sess-old"
	        old_info.working_dir = "/tmp"
	        backend.create_session = AsyncMock(side_effect=[old_info, new_info])
	        backend.get_session_info = AsyncMock(return_value=old_info)
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        await conn.run()

	        # Old hooks should be cleaned up before new session created
	        assert "old_unregister" in calls, "Old session hooks must be unregistered"
	        # New hooks should be fetched for the replacement session
	        assert backend.get_hook_unregister.call_count == 2
	        # On final disconnect, new hooks are NOT cleaned up (only /bundle and /cwd cleanup)
	        assert "new_unregister" not in calls, "New session hooks must NOT be unregistered on plain disconnect"

	    @pytest.mark.asyncio
	    async def test_cwd_command_cleans_old_hooks_and_wires_new(self):
	        """Switching cwd via /command must cleanup old hooks and wire new ones."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        unregister_calls = []

	        ws = make_ws(
	            [
	                {"type": "create_session", "cwd": "/tmp"},
	                {"type": "command", "name": "cwd", "args": ["/new/path"]},
	            ]
	        )
	        old_info = MagicMock()
	        old_info.session_id = "sess-old"
	        old_info.working_dir = "/tmp"
	        new_info = MagicMock()
	        new_info.session_id = "sess-new-cwd"
	        new_info.working_dir = "/new/path"

	        backend = make_backend()
	        backend.create_session = AsyncMock(side_effect=[old_info, new_info])
	        backend.get_session_info = AsyncMock(return_value=old_info)
	        backend.end_session = AsyncMock()
	        backend.get_hook_unregister = MagicMock(
	            side_effect=[
	                lambda: unregister_calls.append("old"),
	                lambda: unregister_calls.append("new"),
	            ]
	        )
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        await conn.run()

	        assert "old" in unregister_calls, "Old session hooks must be unregistered"
	        assert "new" not in unregister_calls, "New session hooks must NOT be unregistered on plain disconnect"

	    @pytest.mark.asyncio
	    async def test_works_without_get_hook_unregister_method(self):
	        """If backend lacks get_hook_unregister, disconnect should still work."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws(
	            [
	                {"type": "create_session", "cwd": "/tmp"},
	            ]
	        )
	        backend = make_backend()
	        # Explicitly remove the method (simulates older backend)
	        if hasattr(backend, "get_hook_unregister"):
	            del backend.get_hook_unregister
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        await conn.run()  # Should not raise

	        assert conn._hook_unregister is None


class TestExecutionSurvivesDisconnect:
	    """Tests for execution surviving WebSocket disconnect (BUG-3, issue #61)."""

	    @pytest.mark.asyncio
	    async def test_active_execution_not_cancelled_on_disconnect(self):
	        """The active execution task must NOT be cancelled on WS disconnect."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        execution_completed = False

	        async def slow_execute(session_id, content, images=None):
	            nonlocal execution_completed
	            await asyncio.sleep(0.05)
	            execution_completed = True

	        ws = make_ws(
	            [
	                {"type": "create_session", "cwd": "/tmp"},
	                {"type": "prompt", "content": "hello"},
	            ]
	        )
	        backend = make_backend()
	        backend.execute = slow_execute
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        await conn.run()

	        # Give the execution a moment to finish (it runs detached)
	        await asyncio.sleep(0.1)
	        assert execution_completed, "Execution must complete despite WS disconnect"

	    @pytest.mark.asyncio
	    async def test_cancel_session_not_called_on_disconnect(self):
	        """cancel_session should NOT be called automatically on disconnect."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws(
	            [
	                {"type": "create_session", "cwd": "/tmp"},
	            ]
	        )
	        backend = make_backend()
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        await conn.run()

	        backend.cancel_session.assert_not_awaited()

	    @pytest.mark.asyncio
	    async def test_explicit_cancel_still_works(self):
	        """User-initiated cancel (via cancel message) must still work."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws(
	            [
	                {"type": "create_session", "cwd": "/tmp"},
	                {"type": "cancel", "level": "graceful"},
	            ]
	        )
	        backend = make_backend()
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        await conn.run()

	        backend.cancel_session.assert_awaited_once()


class TestEventQueueBounded:
	    def test_event_queue_has_maxsize(self):
	        """event_queue must be bounded to prevent unbounded memory growth."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([])
	        backend = make_backend()
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        assert conn.event_queue.maxsize > 0, "event_queue must have a maxsize"

	    def test_event_queue_maxsize_is_10000(self):
	        """event_queue maxsize should be 10000."""
	        from amplifier_distro.server.apps.chat.connection import (
	            _EVENT_QUEUE_MAX_SIZE,
	            ChatConnection,
	        )

	        ws = make_ws([])
	        backend = make_backend()
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        assert _EVENT_QUEUE_MAX_SIZE == 10000
	        assert conn.event_queue.maxsize == _EVENT_QUEUE_MAX_SIZE


class TestSyntheticStreaming:
	    @pytest.mark.asyncio
	    async def test_synthetic_deltas_sent_for_non_streaming_blocks(self):
	        """When content_end arrives with no prior deltas, synthesize chunked deltas."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([])
	        backend = make_backend()
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)

	        # Simulate runtime shape: content_start then content_end (no deltas)
	        await conn.event_queue.put(
	            ("content_block:start", {"block_type": "text", "block_index": 2})
	        )
	        await conn.event_queue.put(
	            (
	                "content_block:end",
	                {
	                    "block_index": 2,
	                    "block": {"type": "text", "text": "Hello world synthetic"},
	                },
	            )
	        )
	        await conn.event_queue.put(_STOP)

	        await conn._event_fanout_loop()

	        sent = [call.args[0] for call in ws.send_json.await_args_list]
	        delta_messages = [m for m in sent if m.get("type") == "content_delta"]
	        # Should have multiple delta messages (chunked at 12 chars)
	        assert len(delta_messages) > 1
	        # Concatenated deltas should reconstruct the text
	        full = "".join(m["delta"] for m in delta_messages)
	        assert full == "Hello world synthetic"
	        # Synthetic chunks should map using block_index (not hard-coded index=0)
	        assert all(m["index"] == 2 for m in delta_messages)

	    @pytest.mark.asyncio
	    async def test_synthetic_deltas_support_object_block_payload(self):
	        """Synthetic chunking reads text/index from object-style block payloads."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([])
	        backend = make_backend()
	        config = make_config()

	        conn = ChatConnection(ws, backend, config)
	        block = type("B", (), {"index": 4, "thinking": "Object payload thinking"})()

	        await conn.event_queue.put(
	            ("content_block:start", {"block_type": "thinking", "block_index": 4})
	        )
	        await conn.event_queue.put(("content_block:end", {"block": block}))
	        await conn.event_queue.put(_STOP)

	        await conn._event_fanout_loop()

	        sent = [call.args[0] for call in ws.send_json.await_args_list]
	        delta_messages = [m for m in sent if m.get("type") == "content_delta"]
	        assert len(delta_messages) > 0
	        full = "".join(m["delta"] for m in delta_messages)
	        assert full == "Object payload thinking"


class TestConnectionRegistry:
	    """Verify the module-level _active_connections registry and broadcast_to_all."""

	    def test_registry_symbols_exported(self):
	        """_active_connections set and broadcast_to_all coroutine must be importable."""
	        from amplifier_distro.server.apps.chat.connection import (
	            _active_connections,
	            broadcast_to_all,
	        )

	        assert isinstance(_active_connections, set)
	        assert callable(broadcast_to_all)

	    @pytest.mark.asyncio
	    async def test_broadcast_sends_json_to_active_connection(self):
	        """broadcast_to_all sends JSON-encoded payload to every active connection."""
	        import json

	        from amplifier_distro.server.apps.chat.connection import (
	            _active_connections,
	            broadcast_to_all,
	        )

	        mock_ws = MagicMock()
	        mock_ws.send_text = AsyncMock()

	        fake_conn = MagicMock()
	        fake_conn._ws = mock_ws

	        _active_connections.add(fake_conn)
	        try:
	            await broadcast_to_all({"type": "session_renamed", "session_id": "s1", "name": "New"})
	        finally:
	            _active_connections.discard(fake_conn)

	        mock_ws.send_text.assert_awaited_once()
	        payload = mock_ws.send_text.call_args[0][0]
	        assert json.loads(payload) == {"type": "session_renamed", "session_id": "s1", "name": "New"}

	    @pytest.mark.asyncio
	    async def test_broadcast_tolerates_failed_connection(self):
	        """broadcast_to_all must not raise when a send fails."""
	        from amplifier_distro.server.apps.chat.connection import (
	            _active_connections,
	            broadcast_to_all,
	        )

	        mock_ws = MagicMock()
	        mock_ws.send_text = AsyncMock(side_effect=Exception("disconnected"))

	        fake_conn = MagicMock()
	        fake_conn._ws = mock_ws

	        _active_connections.add(fake_conn)
	        try:
	            # Must not raise even when the underlying send fails
	            await broadcast_to_all({"type": "ping"})
	        finally:
	            _active_connections.discard(fake_conn)

	    @pytest.mark.asyncio
	    async def test_run_registers_and_deregisters_connection(self):
	        """run() adds self to _active_connections then removes it on exit."""
	        from amplifier_distro.server.apps.chat.connection import (
	            _active_connections,
	            ChatConnection,
	        )

	        # WS that disconnects immediately after accept (no auth, no messages)
	        ws = make_ws([])
	        backend = make_backend()
	        config = make_config(api_key=None)

	        conn = ChatConnection(ws, backend, config)
	        assert conn not in _active_connections  # pre-condition

	        await conn.run()

	        # After run() completes the connection must be gone from the registry
	        assert conn not in _active_connections


class TestOriginCheck:
	    """Verify _auth_handshake origin restriction logic."""

	    @pytest.mark.asyncio
	    async def test_localhost_host_rejects_lan_origin(self):
	        """Default host (127.0.0.1) rejects non-localhost origins with 4003."""
	        from starlette.websockets import WebSocketDisconnect

	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([], headers={"origin": "http://192.168.1.50:8000"})
	        config = make_config(host="127.0.0.1")
	        conn = ChatConnection(ws, make_backend(), config)

	        with pytest.raises(WebSocketDisconnect):
	            await conn._auth_handshake()

	        ws.close.assert_awaited_once_with(4003, "Forbidden origin")

	    @pytest.mark.asyncio
	    async def test_localhost_host_allows_localhost_origin(self):
	        """Default host (127.0.0.1) allows localhost origins."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([], headers={"origin": "http://localhost:8000"})
	        config = make_config(host="127.0.0.1")
	        conn = ChatConnection(ws, make_backend(), config)

	        await conn._auth_handshake()
	        ws.close.assert_not_called()

	    @pytest.mark.asyncio
	    async def test_wildcard_host_allows_lan_origin(self):
	        """host=0.0.0.0 skips origin check — allows LAN origins."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([], headers={"origin": "http://192.168.1.50:8000"})
	        config = make_config(host="0.0.0.0")
	        conn = ChatConnection(ws, make_backend(), config)

	        await conn._auth_handshake()
	        ws.close.assert_not_called()

	    @pytest.mark.asyncio
	    async def test_specific_lan_host_allows_lan_origin(self):
	        """host=192.168.1.50 skips origin check — allows any origin."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([], headers={"origin": "http://10.0.0.5:8000"})
	        config = make_config(host="192.168.1.50")
	        conn = ChatConnection(ws, make_backend(), config)

	        await conn._auth_handshake()
	        ws.close.assert_not_called()

	    @pytest.mark.asyncio
	    async def test_no_origin_header_always_allowed(self):
	        """Non-browser clients (no Origin header) are always allowed."""
	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([], headers={})
	        config = make_config(host="127.0.0.1")
	        conn = ChatConnection(ws, make_backend(), config)

	        await conn._auth_handshake()
	        ws.close.assert_not_called()

	    @pytest.mark.asyncio
	    async def test_localhost_name_host_rejects_lan_origin(self):
	        """host='localhost' (the string) also enforces strict origin check."""
	        from starlette.websockets import WebSocketDisconnect

	        from amplifier_distro.server.apps.chat.connection import ChatConnection

	        ws = make_ws([], headers={"origin": "http://192.168.1.50:8000"})
	        config = make_config(host="localhost")
	        conn = ChatConnection(ws, make_backend(), config)

	        with pytest.raises(WebSocketDisconnect):
	            await conn._auth_handshake()

	        ws.close.assert_awaited_once_with(4003, "Forbidden origin")
