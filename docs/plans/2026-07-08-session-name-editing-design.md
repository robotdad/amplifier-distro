# Inline Session Name Editing Design

**GitHub issue:** https://github.com/microsoft/amplifier-distro/issues/48

## Goal

Allow users to rename sessions inline from the chat app sidebar, overriding the auto-generated name with a user-chosen one.

## Background

Sessions are auto-named by the `hooks-session-naming` hook after 2 turns of conversation. Today, the `SessionCard` component renders session names as static text with no edit affordance — users have no way to override the auto-generated name. The auto-naming hook already guards against overwriting existing names (`metadata.get("name") is not None`), so once a name is set by any means, it sticks.

### Architecture context

- The chat app is a single-file Preact SPA (`distro-server/src/amplifier_distro/server/apps/chat/static/index.html`, ~4,141 lines) using HTM tagged templates.
- `SessionCard` component (line 1720) renders session names as static `<div>` text.
- Name priority chain: `session.name` (from metadata.json) → `session.bundle` → `'new session'`/`'session'`.
- `metadata_persistence.py` is the canonical shared write path with atomic merge-write via `write_metadata(session_dir, metadata)`.
- `SessionBackend` Protocol in `session_backend.py` is the single shared contract all surfaces use (chat, voice, slack, routines, bridge).
- `session_history.py` is read-only and chat-specific — not suitable for write logic.
- No rename API endpoint or `session_renamed` WebSocket message type exists today.

## Approach

**REST mutation + WebSocket propagation + optimistic UI.**

- REST `PATCH` for the mutation: clean, reusable by all surfaces, proper HTTP status codes.
- WebSocket for propagation: instant multi-tab sync, consistent with existing `session_created` and `session_fork` events.
- Optimistic UI update: feels instant regardless of network latency.
- Shared backend layer (not chat-specific): `SessionBackend` Protocol is already the contract all surfaces share, and `metadata_persistence.write_metadata()` is the canonical write path. This means Slack, voice, bridge, and routines get rename for free.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Browser (Preact SPA)                                   │
│                                                         │
│  SessionCard ──pencil click/F2──► Inline <input>        │
│       │                               │                 │
│       │  optimistic update            │  Enter/blur     │
│       ▼                               ▼                 │
│  session Map updated          PATCH /api/sessions/:id   │
│       ▲                               │                 │
│       │  "session_renamed" WS         │                 │
│       │  (multi-tab sync)             │                 │
└───────┼───────────────────────────────┼─────────────────┘
        │                               │
        │                               ▼
┌───────┼───────────────────────────────────────────────┐
│  Server (app.py core router)          │               │
│       │                               │               │
│       │◄── WS broadcast ◄────────────┐│               │
│       │                              ││               │
│       │    PATCH handler ────► SessionBackend          │
│       │                     .update_session_metadata() │
│       │                              │                │
│       │                              ▼                │
│       │                   metadata_persistence        │
│       │                   .write_metadata()           │
│       │                   (atomic merge-write)        │
└───────┼───────────────────────────────────────────────┘
        │
        ▼
   Other open tabs receive WS event, update their session Map
```

## Components

### Backend: SessionBackend.update_session_metadata()

New method added to the `SessionBackend` Protocol, `FoundationBackend`, and `MockBackend`:

```python
async def update_session_metadata(self, session_id: str, updates: dict) -> bool
```

- Delegates to `metadata_persistence.write_metadata()` for atomic merge-write.
- **Active sessions:** resolves path via handle's `project_id` (fast, no scan).
- **Inactive/history sessions:** disk scan fallback (like `_find_transcript()`).
- Returns `True` if session found and written, `False` if not found.

**File:** `distro-server/src/amplifier_distro/server/session_backend.py`

### Backend: PATCH API Route

New shared route in the core router (not chat-specific):

```
PATCH /api/sessions/{session_id}
Body: { "name": "My custom name" }
Response: { "session_id": "...", "name": "My custom name" }
```

- **Validation:** name must be a non-empty string, trimmed, max 100 characters.
- **404** if session doesn't exist.
- **400** for invalid input.
- Protected by existing `verify_api_key` dependency.
- After successful write, emits `session_renamed` WebSocket event to all connected chat clients.

**File:** `distro-server/src/amplifier_distro/server/app.py`

### Backend: WebSocket Broadcast

After successful metadata write, broadcast to all connected chat clients:

```json
{ "type": "session_renamed", "session_id": "...", "name": "My custom name" }
```

### Frontend: SessionCard Inline Editing

**Interaction pattern: explicit trigger with progressive reveal.**

| State | Behavior |
|---|---|
| **Default** | Name renders as static text (unchanged) |
| **Hover (desktop)** | Pencil icon appears right-aligned in the card |
| **Active session** | Pencil icon always visible |
| **Click pencil / F2** | Name text replaced by `<input>` with identical font, underline-only border, all text auto-selected. Confirm (checkmark) and cancel (X) icons appear |
| **Long-press (touch, 500ms)** | Context sheet with "Rename" option, then same inline edit |

**Why explicit trigger over click-to-edit:** The card's primary action is navigation. Making the name itself an edit target creates ambiguity between "open session" and "rename session". Every app that solved this well (ChatGPT, VS Code, Notion, Finder) uses an explicit trigger.

### Frontend: Save/Cancel Behavior

- **Enter** or **blur** → save (optimistic update + PATCH request).
- **Escape** → cancel, restore original name.
- **Empty name** → blocked, subtle shake animation.
- **Tab** → save and advance focus.
- **Success feedback:** 200ms accent color flash (respects `prefers-reduced-motion` — falls back to instant color change).

### Frontend: Error Handling

If PATCH fails (404, 400, network error), roll back the optimistic update to the previous name and show a brief inline error.

### Frontend: Auto-named vs User-named Visual Distinction

- **Auto-generated names:** `color: var(--text-secondary)` — subtly muted.
- **User-named:** `color: var(--text-primary)` — full weight, "owned."
- No badges, no italic, no different font size — just a tonal shift.

### Frontend: WebSocket Listener

On receiving `session_renamed`, update the session in the Map state. Handles multi-tab sync and external name updates.

### Frontend: Search Bug Fix

Add `session.name` to `sessionMatchesFilter()` haystack (line 1212) so users can search/filter sessions by name.

## Data Flow

```
1. User clicks pencil icon or presses F2
2. SessionCard flips to inline edit mode (input replaces text)
3. User types new name, presses Enter
4. Frontend:
   a. Optimistic update — session Map updated immediately, UI reflects new name
   b. PATCH /api/sessions/{session_id} → { "name": "..." }
5. Backend (app.py core router):
   a. Calls backend.update_session_metadata(session_id, {"name": "..."})
   b. FoundationBackend resolves session dir (handle for active, disk scan for inactive)
   c. metadata_persistence.write_metadata() atomic merge-writes to metadata.json
   d. Emits "session_renamed" WebSocket event to all connected clients
6. Other open tabs:
   a. Receive "session_renamed" WS event
   b. Update their session Map — UI reflects new name
7. Auto-naming hook (on subsequent orchestrator:complete):
   a. Reads metadata.json
   b. Sees name already exists (non-null) → skips name generation
```

## Error Handling

| Scenario | Backend | Frontend |
|---|---|---|
| Session not found | 404 response | Roll back optimistic update, show inline error |
| Invalid input (empty/too long) | 400 response | Client-side validation blocks submit; server validates as fallback |
| Network failure | N/A | Roll back optimistic update, show inline error |
| Concurrent rename (two tabs) | Last write wins (atomic merge-write) | WebSocket broadcast updates all tabs to final state |
| metadata.json write failure | 500 response | Roll back optimistic update, show inline error |

## Accessibility

### Keyboard Navigation

- **Tab / Shift+Tab:** Move focus between session cards.
- **Enter / Space:** Navigate to session (primary action, unchanged).
- **F2:** Enter rename mode (matches Finder/VS Code convention).
- **Escape:** Exit rename mode, restore original name.
- **Tab (in edit mode):** Save and advance focus to next session.

### ARIA

- Pencil button: `aria-label="Rename session: {name}"`, `aria-keyshortcuts="F2"`.
- Edit input: `aria-label="Session name"`, `aria-describedby` pointing to hint "Press Enter to save, Escape to cancel."
- `aria-live="polite"` region for announcements: "Editing session name", "Session renamed to X", "Rename cancelled."

### Touch Targets

Pencil button ≥ 44×44px hit area (padding expands hit area without affecting layout).

### Reduced Motion

Save confirmation flash respects `prefers-reduced-motion` — falls back to instant color change.

## Auto-Naming Interaction

No special handling needed. The `hooks-session-naming` hook already checks `metadata.get("name") is not None` before generating a name. Once any name exists — whether auto-generated or user-set — the initial naming path is permanently skipped. No additional `name_source` field is required.

## Files Touched

### Backend

| File | Change |
|---|---|
| `distro-server/src/amplifier_distro/server/session_backend.py` | New `update_session_metadata` method on Protocol, FoundationBackend, and MockBackend |
| `distro-server/src/amplifier_distro/server/app.py` | New `PATCH /api/sessions/{session_id}` route in core router + WebSocket broadcast |

### Frontend

| File | Change |
|---|---|
| `distro-server/src/amplifier_distro/server/apps/chat/static/index.html` | SessionCard inline editing, pencil icon, save/cancel, WebSocket listener, search fix, visual distinction |

## Testing Strategy

- **Backend unit tests:** `update_session_metadata` for active sessions, inactive sessions, and not-found cases. PATCH route validation (empty name, too-long name, valid name, missing session).
- **Frontend manual testing:** Edit flow (pencil click, F2, Enter, Escape, blur, Tab), optimistic update + rollback on error, multi-tab sync via WebSocket, touch long-press, keyboard-only navigation, screen reader announcements.
- **Integration:** End-to-end rename flow verifying metadata.json is updated and auto-naming hook respects the user-set name on subsequent turns.

## Design Rationale

### Why explicit trigger over click-to-edit name
The card's primary action is navigation. Making the name itself an edit target creates ambiguity between "open session" and "rename session." Every app that solved this well (ChatGPT, VS Code, Notion, Finder) uses an explicit trigger.

### Why REST + WebSocket over REST-only or WS-only
- REST for the mutation: clean, reusable by all surfaces, proper HTTP status codes for error handling.
- WebSocket for propagation: instant multi-tab sync, consistent with existing `session_created` and `session_fork` events.
- Optimistic UI update: feels instant regardless of network latency.

### Why shared backend layer over chat-specific
- `session_history.py` is read-only, chat-specific, imported by nothing else.
- `SessionBackend` Protocol is already the contract all surfaces share.
- `metadata_persistence.write_metadata()` is already the canonical write path.
- Putting it in the shared layer means Slack, voice, bridge, and routines get rename for free.

### Why no name_source field
The auto-naming hook already has a firm guard: `metadata.get("name") is not None`. Once any name exists, it won't overwrite. No additional flag needed.

## Open Questions

None — all design decisions have been resolved.