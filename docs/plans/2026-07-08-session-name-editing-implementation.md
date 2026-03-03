# Inline Session Name Editing — Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Allow users to rename sessions inline from the chat app sidebar via a REST PATCH mutation, WebSocket propagation for multi-tab sync, and optimistic UI updates.

**Architecture:** A new `update_session_metadata()` method on the shared `SessionBackend` Protocol delegates to `metadata_persistence.write_metadata()` for atomic merge-writes. A `PATCH /api/sessions/{session_id}` route in the core router validates input, calls the backend, and broadcasts a `session_renamed` WebSocket event to all connected chat clients via a new connection registry. The frontend `SessionCard` component gains an inline edit mode triggered by a pencil icon, F2, or touch long-press, with optimistic updates and rollback on error.

**Tech Stack:** Python 3.11+ / FastAPI (backend), Preact + HTM (frontend SPA), pytest + mocks (testing)

**Design doc:** `docs/plans/2026-07-08-session-name-editing-design.md`
**GitHub issue:** https://github.com/microsoft/amplifier-distro/issues/48

---

## Important Notes

- **All Python commands run from the `distro-server/` directory.**
- **Run tests with:** `uv run pytest tests/<file>.py -v`
- The project uses `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` decorators needed.
- The frontend is a single ~4,141-line file (`distro-server/src/amplifier_distro/server/apps/chat/static/index.html`). All frontend tasks modify this one file.
- Frontend tasks cannot be TDD'd with pytest — they specify manual browser testing steps instead.
- Backend tasks follow strict TDD: write failing test → verify failure → implement → verify pass.

---

## Task 1: Add `update_session_metadata` to SessionBackend Protocol and MockBackend

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/session_backend.py` (lines 160–162 and 242–243)
- Modify: `distro-server/tests/test_session_backend.py`

### Step 1: Write the failing test

Add a new test class at the **end** of `distro-server/tests/test_session_backend.py`:

```python
class TestUpdateSessionMetadata:
    """Verify update_session_metadata on MockBackend and Protocol compliance."""

    async def test_mock_backend_update_session_metadata_returns_true(self):
        """MockBackend.update_session_metadata records the call and returns True."""
        from amplifier_distro.server.session_backend import MockBackend

        backend = MockBackend()
        result = await backend.update_session_metadata("sess-001", {"name": "My Session"})

        assert result is True
        assert any(
            c["method"] == "update_session_metadata" and c["session_id"] == "sess-001"
            for c in backend.calls
        )

    async def test_mock_backend_update_records_updates_dict(self):
        """MockBackend records the full updates dict in the call log."""
        from amplifier_distro.server.session_backend import MockBackend

        backend = MockBackend()
        await backend.update_session_metadata("sess-002", {"name": "Renamed"})

        call = next(c for c in backend.calls if c["method"] == "update_session_metadata")
        assert call["updates"] == {"name": "Renamed"}

    async def test_protocol_has_update_session_metadata(self):
        """SessionBackend Protocol must declare update_session_metadata."""
        from amplifier_distro.server.session_backend import SessionBackend

        assert hasattr(SessionBackend, "update_session_metadata")
```

### Step 2: Run tests to verify they fail

```bash
cd distro-server && uv run pytest tests/test_session_backend.py::TestUpdateSessionMetadata -v
```

Expected: FAIL — `MockBackend` has no `update_session_metadata` method, and `SessionBackend` Protocol does not declare it.

### Step 3: Add method to SessionBackend Protocol

In `distro-server/src/amplifier_distro/server/session_backend.py`, add the following method to the `SessionBackend` Protocol class, **after** the `list_active_sessions` method (after line 162, before the blank line at 163):

```python
    async def update_session_metadata(self, session_id: str, updates: dict) -> bool:
        """Update metadata for a session. Returns True if found and written."""
        ...
```

### Step 4: Add method to MockBackend

In the same file, add the following method to the `MockBackend` class, **after** `end_session` (after line 242, before `get_session_info` at line 244):

```python
    async def update_session_metadata(self, session_id: str, updates: dict) -> bool:
        """Record the call and return True (testing stub)."""
        self.calls.append({
            "method": "update_session_metadata",
            "session_id": session_id,
            "updates": updates,
        })
        return True
```

### Step 5: Run tests to verify they pass

```bash
cd distro-server && uv run pytest tests/test_session_backend.py::TestUpdateSessionMetadata -v
```

Expected: All 3 tests PASS.

### Step 6: Run full test suite to check for regressions

```bash
cd distro-server && uv run pytest tests/test_session_backend.py -v
```

Expected: All existing tests still pass.

### Step 7: Commit

```bash
cd distro-server && git add src/amplifier_distro/server/session_backend.py tests/test_session_backend.py && git commit -m "feat: add update_session_metadata to SessionBackend Protocol and MockBackend

Adds the method stub to the Protocol and a recording implementation
to MockBackend for testing. Part of inline session renaming (#48)."
```

---

## Task 2: Implement `update_session_metadata` on FoundationBackend

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/session_backend.py` (imports at line 26, new method on FoundationBackend)
- Modify: `distro-server/tests/test_session_backend.py`

### Step 1: Write the failing tests

Add the following test class at the **end** of `distro-server/tests/test_session_backend.py`:

```python
class TestFoundationBackendUpdateSessionMetadata:
    """Verify FoundationBackend.update_session_metadata for active, inactive, and missing sessions."""

    async def test_active_session_writes_metadata(self, bridge_backend, tmp_path):
        """Active session: resolves dir via handle, calls write_metadata."""
        handle = _make_mock_handle("sess-active-001")
        handle.project_id = "proj-a"
        bridge_backend._sessions["sess-active-001"] = handle

        # Create the session directory on disk
        session_dir = tmp_path / "proj-a" / "sessions" / "sess-active-001"
        session_dir.mkdir(parents=True)

        from amplifier_distro.server.session_backend import FoundationBackend

        with patch(
            "amplifier_distro.server.session_backend.Path"
        ) as MockPath:
            # Make Path(AMPLIFIER_HOME).expanduser() return tmp_path
            mock_home = MagicMock()
            mock_home.expanduser.return_value = tmp_path
            mock_home.__truediv__ = tmp_path.__truediv__
            MockPath.return_value = mock_home

            with patch(
                "amplifier_distro.server.session_backend.write_metadata"
            ) as mock_write:
                result = await FoundationBackend.update_session_metadata(
                    bridge_backend, "sess-active-001", {"name": "Renamed"}
                )

        assert result is True
        mock_write.assert_called_once()
        call_args = mock_write.call_args
        assert call_args[0][1] == {"name": "Renamed"}

    async def test_inactive_session_scans_disk(self, bridge_backend, tmp_path):
        """Inactive session: falls back to disk scan like _find_transcript."""
        # No handle in _sessions — session is inactive
        session_dir = tmp_path / "proj-x" / "sessions" / "sess-inactive-001"
        session_dir.mkdir(parents=True)

        from amplifier_distro.server.session_backend import FoundationBackend

        with patch(
            "amplifier_distro.server.session_backend.Path"
        ) as MockPath:
            mock_home = MagicMock()
            mock_home.expanduser.return_value = tmp_path
            MockPath.return_value = mock_home

            # Make tmp_path / PROJECTS_DIR resolve correctly
            projects_dir = tmp_path / "projects"
            projects_dir.mkdir(parents=True, exist_ok=True)
            # Move the project dir under projects/
            import shutil
            target = projects_dir / "proj-x"
            if not target.exists():
                shutil.copytree(tmp_path / "proj-x", target)

            mock_home.__truediv__ = lambda self, other: tmp_path / other

            with patch(
                "amplifier_distro.server.session_backend.write_metadata"
            ) as mock_write:
                result = await FoundationBackend.update_session_metadata(
                    bridge_backend, "sess-inactive-001", {"name": "Offline Rename"}
                )

        assert result is True
        mock_write.assert_called_once()

    async def test_missing_session_returns_false(self, bridge_backend, tmp_path):
        """Session not found anywhere: returns False."""
        from amplifier_distro.server.session_backend import FoundationBackend

        with patch(
            "amplifier_distro.server.session_backend.Path"
        ) as MockPath:
            mock_home = MagicMock()
            mock_home.expanduser.return_value = tmp_path
            MockPath.return_value = mock_home

            # Create empty projects dir
            projects_dir = tmp_path / "projects"
            projects_dir.mkdir(parents=True, exist_ok=True)
            mock_home.__truediv__ = lambda self, other: tmp_path / other

            result = await FoundationBackend.update_session_metadata(
                bridge_backend, "sess-nonexistent", {"name": "Ghost"}
            )

        assert result is False
```

### Step 2: Run tests to verify they fail

```bash
cd distro-server && uv run pytest tests/test_session_backend.py::TestFoundationBackendUpdateSessionMetadata -v
```

Expected: FAIL — `FoundationBackend` has no `update_session_metadata` method.

### Step 3: Add the import

In `distro-server/src/amplifier_distro/server/session_backend.py`, change line 26 from:

```python
from amplifier_distro.metadata_persistence import register_metadata_hooks
```

to:

```python
from amplifier_distro.metadata_persistence import register_metadata_hooks, write_metadata
```

### Step 4: Implement `update_session_metadata` on FoundationBackend

Add the following method to the `FoundationBackend` class, **after** the `end_session` method (after line 921, before `async def stop`):

```python
    async def update_session_metadata(self, session_id: str, updates: dict) -> bool:
        """Update metadata for a session. Returns True if found and written.

        Active sessions: resolves session dir via the handle's project_id.
        Inactive sessions: scans ~/.amplifier/projects/ (same as _find_transcript).
        """
        # Fast path: active session with a known handle
        handle = self._sessions.get(session_id)
        if handle is not None:
            session_dir = (
                Path(AMPLIFIER_HOME).expanduser()
                / PROJECTS_DIR
                / handle.project_id
                / "sessions"
                / session_id
            )
            if session_dir.exists():
                write_metadata(session_dir, updates)
                return True

        # Slow path: scan all project directories (inactive/history sessions)
        projects_dir = Path(AMPLIFIER_HOME).expanduser() / PROJECTS_DIR
        if not projects_dir.exists():
            return False

        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            session_dir = project_dir / "sessions" / session_id
            if session_dir.exists():
                write_metadata(session_dir, updates)
                return True

        return False
```

### Step 5: Run tests to verify they pass

```bash
cd distro-server && uv run pytest tests/test_session_backend.py::TestFoundationBackendUpdateSessionMetadata -v
```

Expected: All 3 tests PASS.

### Step 6: Run full test suite

```bash
cd distro-server && uv run pytest tests/test_session_backend.py -v
```

Expected: All tests pass.

### Step 7: Commit

```bash
cd distro-server && git add src/amplifier_distro/server/session_backend.py tests/test_session_backend.py && git commit -m "feat: implement update_session_metadata on FoundationBackend

Active sessions resolve via handle.project_id (fast path).
Inactive sessions fall back to disk scan like _find_transcript.
Delegates to metadata_persistence.write_metadata for atomic merge-write.
Part of inline session renaming (#48)."
```

---

## Task 3: Add connection registry to ChatConnection

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/chat/connection.py`

### Step 1: Add module-level registry and broadcast helper

In `distro-server/src/amplifier_distro/server/apps/chat/connection.py`, add the following **after** line 39 (`_VALID_SESSION_ID = re.compile(...)`) and **before** the `class ChatConnection:` line (line 42):

```python

# Module-level registry of all active WebSocket connections.
# Used by broadcast_to_all() to push events like session_renamed.
_active_connections: set[ChatConnection] = set()


async def broadcast_to_all(message: dict) -> None:
    """Send a JSON message to every connected chat WebSocket client.

    Silently skips connections that fail (e.g. already disconnected).
    """
    import json as _json

    payload = _json.dumps(message)
    for conn in list(_active_connections):
        try:
            await conn._ws.send_text(payload)
        except Exception:  # noqa: BLE001
            pass
```

### Step 2: Register connection in `run()` try block

In the same file, modify the `run()` method. Find lines 74–80 (the `try:` block that calls `_receive_loop`):

```python
        try:
            await self._receive_loop()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            logger.warning("ChatConnection receive error", exc_info=True)
        finally:
```

Replace with:

```python
        _active_connections.add(self)
        try:
            await self._receive_loop()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            logger.warning("ChatConnection receive error", exc_info=True)
        finally:
            _active_connections.discard(self)
```

That means adding `_active_connections.add(self)` right **before** the existing `try:` at line 74, and adding `_active_connections.discard(self)` as the **first line** inside the `finally:` block at line 80.

### Step 3: Verify no syntax errors

```bash
cd distro-server && uv run python -c "from amplifier_distro.server.apps.chat.connection import broadcast_to_all; print('OK')"
```

Expected: prints `OK` with no errors.

### Step 4: Commit

```bash
cd distro-server && git add src/amplifier_distro/server/apps/chat/connection.py && git commit -m "feat: add connection registry and broadcast_to_all to ChatConnection

Module-level set tracks active WebSocket connections. broadcast_to_all()
pushes JSON messages to all connected clients. Registration happens in
run() with cleanup in finally block. Part of inline session renaming (#48)."
```

---

## Task 4: Add PATCH /api/sessions/{session_id} route

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/app.py` (inside `_setup_bridge_routes()`, before line 489)

### Step 1: Add the PATCH route

In `distro-server/src/amplifier_distro/server/app.py`, inside `_setup_bridge_routes()`, add the following new route **after** the `execute_prompt` route handler (after line 488, before the `_setup_memory_routes` method at line 490). Insert it between the last `except` return of `execute_prompt` and the blank line before `_setup_memory_routes`:

```python

        @self._core_router.patch(
            "/sessions/{session_id}",
            response_model=None,
            dependencies=[Depends(verify_api_key)],
        )
        async def rename_session(session_id: str, request: Request) -> JSONResponse:
            """Rename a session by updating its metadata."""
            from amplifier_distro.server.services import get_services

            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid JSON body"},
                )

            name = body.get("name") if isinstance(body, dict) else None
            if not isinstance(name, str) or not name.strip():
                return JSONResponse(
                    status_code=400,
                    content={"error": "name must be a non-empty string"},
                )

            name = name.strip()
            if len(name) > 100:
                return JSONResponse(
                    status_code=400,
                    content={"error": "name must be 100 characters or fewer"},
                )

            try:
                services = get_services()
                found = await services.backend.update_session_metadata(
                    session_id, {"name": name}
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Session rename failed: %s", e, exc_info=True)
                return JSONResponse(
                    status_code=500,
                    content={"error": str(e), "type": type(e).__name__},
                )

            if not found:
                return JSONResponse(
                    status_code=404,
                    content={"error": f"Session {session_id} not found"},
                )

            # Broadcast to all connected chat WebSocket clients
            try:
                from amplifier_distro.server.apps.chat.connection import (
                    broadcast_to_all,
                )

                await broadcast_to_all({
                    "type": "session_renamed",
                    "session_id": session_id,
                    "name": name,
                })
            except Exception:  # noqa: BLE001
                logger.debug("WebSocket broadcast skipped", exc_info=True)

            return JSONResponse(
                content={"session_id": session_id, "name": name},
            )
```

### Step 2: Verify no syntax errors

```bash
cd distro-server && uv run python -c "from amplifier_distro.server.app import DistroServer; print('OK')"
```

Expected: prints `OK`.

### Step 3: Commit

```bash
cd distro-server && git add src/amplifier_distro/server/app.py && git commit -m "feat: add PATCH /api/sessions/{session_id} route for renaming

Validates name (non-empty, trimmed, max 100 chars), calls
backend.update_session_metadata(), broadcasts session_renamed via
WebSocket to all connected chat clients. Returns 400/404/500 on errors.
Part of inline session renaming (#48)."
```

---

## Task 5: Handle `session_renamed` WebSocket message in frontend

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/chat/static/index.html`

### Step 1: Add handler in the background (non-active) branch

In `distro-server/src/amplifier_distro/server/apps/chat/static/index.html`, find the `session_fork` handler in the background branch (line 2309):

```javascript
        if (msg.type === 'session_fork') {
          applySessionForkLineage(msg);
        }
```

Add the following **before** this `session_fork` block (i.e., after line 2307, before line 2309):

```javascript
        if (msg.type === 'session_renamed') {
          setSessions(prev => {
            const next = new Map(prev);
            // Find the session by session_id (the Map key is the sessionKey, not the id)
            for (const [key, s] of next) {
              if (s.sessionId === msg.session_id) {
                next.set(key, { ...s, name: msg.name });
                break;
              }
            }
            return next;
          });
          return;
        }
```

### Step 2: Add handler in the active session switch block

Find the `pong` case in the active session switch block (line 2631-2632):

```javascript
        case 'pong':
          break;
```

Add the following **before** the `case 'pong':` line:

```javascript
        case 'session_renamed':
          setSessions(prev => {
            const next = new Map(prev);
            for (const [key, s] of next) {
              if (s.sessionId === msg.session_id) {
                next.set(key, { ...s, name: msg.name });
                break;
              }
            }
            return next;
          });
          break;
```

### Step 3: Manual testing

1. Start the dev server: `cd distro-server && uv run amplifier-server`
2. Open two browser tabs to the chat app
3. Open browser DevTools console in Tab A and run:
   ```javascript
   // Simulate receiving a session_renamed WebSocket message
   // (to be replaced with real PATCH calls once the UI is wired)
   ```
4. Verify no console errors on page load

### Step 4: Commit

```bash
cd distro-server && git add src/amplifier_distro/server/apps/chat/static/index.html && git commit -m "feat: handle session_renamed WebSocket message in frontend

Updates the session Map state when receiving a session_renamed event,
enabling multi-tab sync for renamed sessions. Handles both background
and active session branches. Part of inline session renaming (#48)."
```

---

## Task 6: Add pencil icon and inline edit to SessionCard

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/chat/static/index.html`

### Step 1: Add CSS styles for inline editing

In `distro-server/src/amplifier_distro/server/apps/chat/static/index.html`, find the `.session-card-meta` rule (line 333):

```css
    .session-card-meta { font-size: 11px; color: var(--text-muted); display: flex; gap: 6px; align-items: center; margin-top: 2px; }
```

Add the following **after** this line (and before `.session-stale-badge` at line 334):

```css
    .session-card-name-row { display: flex; align-items: center; gap: 4px; min-width: 0; }
    .session-card-name-row .session-card-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .session-rename-trigger {
      background: none; border: none; padding: 4px; margin: -4px; cursor: pointer;
      color: var(--text-muted); opacity: 0; transition: opacity 0.15s;
      display: inline-flex; align-items: center; justify-content: center;
      min-width: 44px; min-height: 44px; /* touch target */
      flex-shrink: 0;
    }
    .session-card:hover .session-rename-trigger,
    .session-card.active .session-rename-trigger { opacity: 1; }
    .session-rename-trigger:hover { color: var(--accent-blue); }
    .session-name-input {
      font-size: 12px; font-weight: 500; color: var(--text-primary);
      background: transparent; border: none; border-bottom: 1px solid var(--accent-blue);
      outline: none; padding: 0; margin: 0; width: 100%; font-family: inherit;
    }
    .session-name-editing .session-edit-actions {
      display: inline-flex; align-items: center; gap: 2px;
    }
    .session-edit-actions { display: none; }
    .session-edit-actions button {
      background: none; border: none; padding: 2px 4px; cursor: pointer;
      color: var(--text-muted); font-size: 12px;
    }
    .session-edit-actions button:hover { color: var(--accent-blue); }
    @keyframes shake {
      0%, 100% { transform: translateX(0); }
      20%, 60% { transform: translateX(-4px); }
      40%, 80% { transform: translateX(4px); }
    }
    .session-name-shake { animation: shake 0.3s ease-in-out; }
    @keyframes accent-flash {
      0% { background-color: transparent; }
      50% { background-color: rgba(59, 130, 246, 0.15); }
      100% { background-color: transparent; }
    }
    .session-name-saved { animation: accent-flash 0.2s ease-in-out; }
    @media (prefers-reduced-motion: reduce) {
      .session-name-shake { animation: none; }
      .session-name-saved { animation: none; background-color: rgba(59, 130, 246, 0.15); transition: background-color 0.1s; }
    }
```

### Step 2: Rewrite `SessionCard` component with inline editing

Find the `SessionCard` function (line 1720–1760). Replace the entire function with:

```javascript
  function SessionCard({ session, isActive, onClick }) {
    const [isEditing, setIsEditing] = useState(false);
    const [editValue, setEditValue] = useState('');
    const [shaking, setShaking] = useState(false);
    const [saved, setSaved] = useState(false);
    const inputRef = useRef(null);
    const originalNameRef = useRef('');

    const statusIcon =
      session.status === 'running' ? '\u27f3 ' :
      session.status === 'connecting' ? '\u25cc ' :
      session.status === 'loading_history' ? '\u25cc ' :
      session.status === 'history' ? '\u25f4 ' :
      session.status === 'error' ? '\u2717 ' :
      '';
    const metaText = session.source === 'history'
      ? `${session.messageCount || session.turnCount || 0} msgs`
      : `turn ${session.turnCount || 0}`;
    const preview = session.lastUserMessage || null;
    const sessionName =
      session.name
      || session.bundle
      || (
        session.source !== 'history' && (session.turnCount || 0) === 0
          ? 'new session'
          : 'session'
      );

    const startEditing = useCallback((e) => {
      if (e) { e.stopPropagation(); e.preventDefault(); }
      originalNameRef.current = sessionName;
      setEditValue(sessionName);
      setIsEditing(true);
    }, [sessionName]);

    const cancelEditing = useCallback(() => {
      setIsEditing(false);
      setEditValue('');
    }, []);

    const saveEdit = useCallback(async () => {
      const trimmed = editValue.trim();
      if (!trimmed) {
        setShaking(true);
        setTimeout(() => setShaking(false), 300);
        return;
      }
      if (trimmed === originalNameRef.current) {
        cancelEditing();
        return;
      }

      // Optimistic update
      setIsEditing(false);
      setSessions(prev => {
        const next = new Map(prev);
        for (const [key, s] of next) {
          if (s.sessionId === session.sessionId) {
            next.set(key, { ...s, name: trimmed, nameSource: 'user' });
            break;
          }
        }
        return next;
      });

      try {
        const headers = { 'Content-Type': 'application/json' };
        const apiKey = localStorage.getItem('amplifier_api_key');
        if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`;
        const resp = await fetch(`/api/sessions/${session.sessionId}`, {
          method: 'PATCH',
          headers,
          body: JSON.stringify({ name: trimmed }),
        });
        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}`);
        }
        // Flash success
        setSaved(true);
        setTimeout(() => setSaved(false), 200);
      } catch (err) {
        console.error('Rename failed:', err);
        // Rollback
        setSessions(prev => {
          const next = new Map(prev);
          for (const [key, s] of next) {
            if (s.sessionId === session.sessionId) {
              next.set(key, { ...s, name: originalNameRef.current });
              break;
            }
          }
          return next;
        });
      }
    }, [editValue, session.sessionId, cancelEditing]);

    const handleKeyDown = useCallback((e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        saveEdit();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        cancelEditing();
      } else if (e.key === 'Tab') {
        e.preventDefault();
        saveEdit();
      }
    }, [saveEdit, cancelEditing]);

    // Focus and select input text when entering edit mode
    useEffect(() => {
      if (isEditing && inputRef.current) {
        inputRef.current.focus();
        inputRef.current.select();
      }
    }, [isEditing]);

    const cardClass = 'session-card'
      + (isActive ? ' active' : '')
      + (isEditing ? ' session-name-editing' : '')
      + (saved ? ' session-name-saved' : '');

    return html`
      <div class=${cardClass} onClick=${isEditing ? undefined : onClick}>
        <div class="session-card-name-row">
          ${isEditing
            ? html`
                <input
                  ref=${inputRef}
                  class=${'session-name-input' + (shaking ? ' session-name-shake' : '')}
                  value=${editValue}
                  onInput=${(e) => setEditValue(e.target.value)}
                  onKeyDown=${handleKeyDown}
                  onBlur=${saveEdit}
                  aria-label="Session name"
                  maxlength="100"
                />
                <span class="session-edit-actions">
                  <button onMouseDown=${(e) => { e.preventDefault(); saveEdit(); }} title="Save" aria-label="Save session name">\u2713</button>
                  <button onMouseDown=${(e) => { e.preventDefault(); cancelEditing(); }} title="Cancel" aria-label="Cancel rename">\u2717</button>
                </span>
              `
            : html`
                <div class="session-card-name">
                  ${statusIcon}${sessionName}
                  ${session.pendingApproval ? html`<${Icon} name="bell" className="sm" />` : ''}
                </div>
                <button
                  class="session-rename-trigger"
                  onClick=${startEditing}
                  aria-label=${'Rename session: ' + sessionName}
                  aria-keyshortcuts="F2"
                  title="Rename session"
                ><${Icon} name="edit-2" className="sm" /></button>
              `
          }
        </div>
        <div class="session-card-cwd session-card-cwd-row" title=${session.cwd || '~'}>
          <${Icon} name="folder" className="sm" />
          <span class="session-card-line-text">${session.cwd || '~'}</span>
        </div>
        <div class="session-card-meta">
          <span>${metaText}</span>
          ${session.hasExternalUpdate
            ? html`<span class="session-stale-badge ${session.isNewSession ? 'new-session' : ''}">${session.isNewSession ? 'New' : 'Updated'}</span>`
            : null}
        </div>
        ${preview ? html`<div class="session-card-cwd" title=${session.lastUserMessage || ''}>${preview}</div>` : null}
      </div>
    `;
  }
```

> **Note:** This component uses `setSessions` from the parent scope. In the existing codebase, `SessionCard` is rendered inside the main `App` component where `setSessions` is in scope. The `setSessions` call in `saveEdit` will work because JavaScript closures capture the enclosing scope. If `setSessions` is NOT in the component's closure scope, you will need to pass it as a prop: `{ session, isActive, onClick, setSessions }`. Check by searching for `<${SessionCard}` to see how it's rendered and whether `setSessions` is accessible.

### Step 3: Verify `setSessions` is accessible

Search for `<${SessionCard}` in the HTML file to confirm how it's called. The `SessionCard` renders inside a component that has `setSessions` in scope. If not, add `setSessions` as a prop.

### Step 4: Manual testing

1. Start the dev server and open the chat app
2. Hover over a session card in the sidebar — pencil icon should appear
3. Click the pencil icon — name should become an editable input with text selected
4. Type a new name and press Enter — name should update with a brief blue flash
5. Click pencil, then press Escape — should cancel without changes
6. Click pencil, clear the input, press Enter — should show a shake animation
7. Check the active session card — pencil icon should always be visible (not just on hover)

### Step 5: Commit

```bash
cd distro-server && git add src/amplifier_distro/server/apps/chat/static/index.html && git commit -m "feat: add inline session name editing to SessionCard

Pencil icon appears on hover (always visible on active session).
Click enters edit mode with auto-selected text. Enter/blur saves with
optimistic update + PATCH call, Escape cancels. Empty name rejected with
shake animation. Success shows 200ms accent flash. Error rolls back.
Part of inline session renaming (#48)."
```

---

## Task 7: Add F2 keyboard shortcut and long-press for touch

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/chat/static/index.html`

### Step 1: Add F2 handler to SessionCard

In the `SessionCard` component (as modified in Task 6), find the `handleKeyDown` callback. This handles keys **inside** the edit input. For F2 on the **card itself**, we need a keydown handler on the outer `<div>`.

Add a new callback inside the `SessionCard` component, after the `handleKeyDown` callback:

```javascript
    const handleCardKeyDown = useCallback((e) => {
      if (e.key === 'F2' && !isEditing) {
        e.preventDefault();
        startEditing(e);
      }
    }, [isEditing, startEditing]);
```

Then update the outer `<div>` in the return template. Find:

```javascript
      <div class=${cardClass} onClick=${isEditing ? undefined : onClick}>
```

Replace with:

```javascript
      <div class=${cardClass} onClick=${isEditing ? undefined : onClick} onKeyDown=${handleCardKeyDown} tabindex="0">
```

### Step 2: Add long-press handler for touch

Add a long-press handler inside the `SessionCard` component. Add these after the `handleCardKeyDown` callback:

```javascript
    const longPressTimerRef = useRef(null);

    const handleTouchStart = useCallback((e) => {
      longPressTimerRef.current = setTimeout(() => {
        longPressTimerRef.current = null;
        startEditing(null);
      }, 500);
    }, [startEditing]);

    const handleTouchEnd = useCallback(() => {
      if (longPressTimerRef.current) {
        clearTimeout(longPressTimerRef.current);
        longPressTimerRef.current = null;
      }
    }, []);

    const handleTouchMove = useCallback(() => {
      if (longPressTimerRef.current) {
        clearTimeout(longPressTimerRef.current);
        longPressTimerRef.current = null;
      }
    }, []);
```

Update the outer `<div>` again to include touch handlers:

```javascript
      <div class=${cardClass} onClick=${isEditing ? undefined : onClick} onKeyDown=${handleCardKeyDown} onTouchStart=${handleTouchStart} onTouchEnd=${handleTouchEnd} onTouchMove=${handleTouchMove} tabindex="0">
```

### Step 3: Manual testing

1. Start the dev server, open the chat app
2. Use Tab to focus a session card, then press F2 — should enter edit mode
3. Press Enter to save, then F2 again on the same card
4. Press Escape to cancel
5. On a touch device (or using Chrome DevTools device emulation): long-press a session card for 500ms — should enter edit mode
6. Short tap should still navigate (not enter edit mode)

### Step 4: Commit

```bash
cd distro-server && git add src/amplifier_distro/server/apps/chat/static/index.html && git commit -m "feat: add F2 keyboard shortcut and touch long-press for session rename

F2 on a focused session card enters rename mode (matches VS Code/Finder).
500ms long-press on touch devices enters rename mode.
Tab in edit mode saves and advances focus.
Part of inline session renaming (#48)."
```

---

## Task 8: Auto-named vs user-named visual distinction

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/chat/static/index.html`

### Step 1: Add CSS for auto-named style

Find the `.session-card-name` rule (line 329):

```css
    .session-card-name { font-size: 12px; font-weight: 500; color: var(--text-primary); }
```

Add the following **after** this line:

```css
    .session-card-name.auto-named { color: var(--text-secondary); }
```

### Step 2: Update SessionCard to apply the auto-named class

In the `SessionCard` component (as modified in Tasks 6-7), find where the name is rendered in the non-editing branch:

```javascript
                <div class="session-card-name">
                  ${statusIcon}${sessionName}
```

Replace with:

```javascript
                <div class=${'session-card-name' + (!session.name ? ' auto-named' : '')}>
                  ${statusIcon}${sessionName}
```

The logic: if `session.name` is falsy (null/undefined — auto-named sessions derive their display name from `session.bundle` or the fallback), the card gets the muted `auto-named` class. If `session.name` is set (either by the auto-naming hook or user rename), it gets the default `--text-primary` color.

### Step 3: Manual testing

1. Start the dev server, open the chat app
2. Sessions with no explicit name (showing "new session" or bundle name fallback) should appear in `--text-secondary` color (slightly muted)
3. Rename a session via the pencil icon — after saving, the name should appear in `--text-primary` color (full weight)

### Step 4: Commit

```bash
cd distro-server && git add src/amplifier_distro/server/apps/chat/static/index.html && git commit -m "feat: visual distinction between auto-named and user-named sessions

Auto-generated names (no session.name set) render in --text-secondary.
User-named sessions render in --text-primary. Just a tonal shift,
no badges or font changes. Part of inline session renaming (#48)."
```

---

## Task 9: Fix `sessionMatchesFilter` to include session name

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/chat/static/index.html`

### Step 1: Add `s.name` to the haystack array

In `distro-server/src/amplifier_distro/server/apps/chat/static/index.html`, find the `sessionMatchesFilter` function's haystack array (line 1212–1224):

```javascript
    const haystack = [
      sessionKey,
      s.sessionId,
      s.cwd,
      s.bundle,
      s.lastUserMessage,
      s.source,
      s.status,
      s.turnCount,
      s.messageCount,
      s.parentSessionId,
      s.spawnAgent,
    ]
```

Add `s.name,` as the **third** element (after `s.sessionId,` and before `s.cwd,`):

```javascript
    const haystack = [
      sessionKey,
      s.sessionId,
      s.name,
      s.cwd,
      s.bundle,
      s.lastUserMessage,
      s.source,
      s.status,
      s.turnCount,
      s.messageCount,
      s.parentSessionId,
      s.spawnAgent,
    ]
```

### Step 2: Manual testing

1. Start the dev server, open the chat app
2. Rename a session to something unique (e.g., "MyUniqueProject")
3. Type "myunique" in the session filter/search bar
4. The renamed session should appear in the filtered results
5. Clear the filter — all sessions should reappear

### Step 3: Commit

```bash
cd distro-server && git add src/amplifier_distro/server/apps/chat/static/index.html && git commit -m "fix: include session name in sessionMatchesFilter haystack

Adds s.name to the search haystack so users can find sessions by their
custom name. Previously only searched sessionId, cwd, bundle, etc.
Part of inline session renaming (#48)."
```

---

## Task 10: Accessibility enhancements

**Files:**
- Modify: `distro-server/src/amplifier_distro/server/apps/chat/static/index.html`

### Step 1: Add screen reader hint element

In the `SessionCard` component, find the edit input in the `isEditing` branch. Add an `aria-describedby` hint. First, add a hidden hint element inside the editing branch, right after the `<input>`:

Find:
```javascript
                <input
                  ref=${inputRef}
                  class=${'session-name-input' + (shaking ? ' session-name-shake' : '')}
                  value=${editValue}
                  onInput=${(e) => setEditValue(e.target.value)}
                  onKeyDown=${handleKeyDown}
                  onBlur=${saveEdit}
                  aria-label="Session name"
                  maxlength="100"
                />
```

Replace with:
```javascript
                <input
                  ref=${inputRef}
                  class=${'session-name-input' + (shaking ? ' session-name-shake' : '')}
                  value=${editValue}
                  onInput=${(e) => setEditValue(e.target.value)}
                  onKeyDown=${handleKeyDown}
                  onBlur=${saveEdit}
                  aria-label="Session name"
                  aria-describedby="session-rename-hint"
                  maxlength="100"
                />
                <span id="session-rename-hint" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)">Press Enter to save, Escape to cancel</span>
```

### Step 2: Add aria-live region for announcements

Add a CSS rule for the live region. Find the `@media (prefers-reduced-motion: reduce)` block added in Task 6. Add the following **after** it:

```css
    .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; }
```

Then, in the `SessionCard` component, add a state for announcements and an aria-live region. Add this state at the top of the component (after the other `useState` calls):

```javascript
    const [announcement, setAnnouncement] = useState('');
```

In the `startEditing` callback, after `setIsEditing(true);`, add:
```javascript
      setAnnouncement('Editing session name');
```

In the `cancelEditing` callback, after `setEditValue('');`, add:
```javascript
      setAnnouncement('Rename cancelled');
```

In the `saveEdit` callback, after `setSaved(true);` (inside the success branch), add:
```javascript
        setAnnouncement('Session renamed to ' + trimmed);
```

Add the aria-live region at the **end** of the component's return template, just before the closing `</div>`:

```javascript
        <div class="sr-only" aria-live="polite" role="status">${announcement}</div>
```

### Step 3: Manual testing

1. Start the dev server, open the chat app
2. Using a screen reader (VoiceOver on macOS, or browser accessibility tools):
   - Tab to a session card
   - Press F2 — should announce "Editing session name"
   - Type a name, press Enter — should announce "Session renamed to [name]"
   - Press F2, then Escape — should announce "Rename cancelled"
3. Verify the pencil button has the correct aria-label ("Rename session: [name]")
4. Verify the edit input has aria-label "Session name" and the describedby hint reads out

### Step 4: Commit

```bash
cd distro-server && git add src/amplifier_distro/server/apps/chat/static/index.html && git commit -m "feat: add accessibility enhancements for session rename

- aria-label on pencil button with session name
- aria-keyshortcuts='F2' on pencil button
- aria-label='Session name' on edit input
- aria-describedby hint: 'Press Enter to save, Escape to cancel'
- aria-live='polite' announcements for edit/save/cancel
- Touch target >= 44x44px for pencil button (set in Task 6 CSS)
Part of inline session renaming (#48)."
```

---

## Summary of Commits

| # | Message | Files |
|---|---------|-------|
| 1 | `feat: add update_session_metadata to SessionBackend Protocol and MockBackend` | `session_backend.py`, `test_session_backend.py` |
| 2 | `feat: implement update_session_metadata on FoundationBackend` | `session_backend.py`, `test_session_backend.py` |
| 3 | `feat: add connection registry and broadcast_to_all to ChatConnection` | `connection.py` |
| 4 | `feat: add PATCH /api/sessions/{session_id} route for renaming` | `app.py` |
| 5 | `feat: handle session_renamed WebSocket message in frontend` | `index.html` |
| 6 | `feat: add inline session name editing to SessionCard` | `index.html` |
| 7 | `feat: add F2 keyboard shortcut and touch long-press for session rename` | `index.html` |
| 8 | `feat: visual distinction between auto-named and user-named sessions` | `index.html` |
| 9 | `fix: include session name in sessionMatchesFilter haystack` | `index.html` |
| 10 | `feat: add accessibility enhancements for session rename` | `index.html` |