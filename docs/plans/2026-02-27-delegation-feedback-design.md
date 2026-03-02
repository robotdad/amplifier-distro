# Delegation Feedback Design

## Goal
Add visual and vocal feedback to the distro-voice app when the voice agent delegates to sub-agents, so users have clear indication that work is occurring during delegation rather than experiencing silence.

## Background
Currently, when the voice agent delegates to a sub-agent, users experience silence with no indication work is occurring. Delegations can take several seconds; without feedback the app feels frozen or broken. SSE delegation events already exist on the server but are not wired to the frontend. This design closes that gap by surfacing delegation activity through both the UI and the voice channel.

## Approach
**Option 2 — Full live feed (~3-4 hrs):** Wire existing SSE delegation events to the frontend UI, bubble child session events up to the parent SSE stream via an opt-in server-side hook, and add deterministic vocal acknowledgement via data channel injection.

The key design principle is **additive-only changes to shared code**. The new `event_forwarder` kwarg defaults to `None`, leaving chat, Slack, and every other app completely unaffected. Voice-specific wiring lives in voice-specific files.

## Architecture

**Server (`spawn_registration.py` — shared, additive only):**
Add an optional `event_forwarder` kwarg (defaults to `None`) to `register_spawning()`. When provided, the spawned child bundle includes a lightweight forwarding hook that pushes child session events to the parent's SSE event queue, tagging each with a `delegating_agent` field. When not provided, behaviour is completely unchanged. `FoundationBackend.create_session()` threads the new kwarg through — also purely additive.

**Voice-specific wiring (`connection.py` — voice-only):**
`VoiceConnection.create()` already passes `exclude_tools=["delegate"]` as voice-specific config. It gains a parallel `event_forwarder` — a closure over the voice session's event queue push function — passed alongside it. All voice-specific concerns stay in this file; shared code is untouched beyond the new optional parameter.

**Frontend state (`useAmplifierEvents`):**
Currently logs SSE events to console and discards them. Extended to maintain a `delegationState` object: `{ active, agentName, startTime, tools[] }`. Updated by:
- `session_fork` → set `active=true`, `agentName`
- `tool_call` with `delegating_agent` set → append tool name to `tools[]`
- `delegate:agent_completed` → set `active=false`, snapshot final state

Calls `onDelegationStart` / `onDelegationEnd` callbacks on the root `VoiceApp` component rather than logging.

**Vocal injection (`useChatMessages`):**
~10 lines in `handleDataChannelEvent`. When `response.output_item.done` fires with `item.name === 'delegate'`, extract the agent name from call arguments, then send `conversation.item.create` (assistant acknowledgement text) + `response.create` via the data channel before firing the tool POST. Runs concurrently with the POST so acknowledgement audio plays during execution.

## Components

### `DelegationTranscriptEntry`
New bubble type injected into the transcript flow when `session_fork` fires. Shows animated `"Delegating to filesystem-agent..."` while active. When `delegate:agent_completed` fires, collapses to a compact persistent summary: `Delegated to filesystem-agent · 3 tools · 2.4s`. Expandable to show the ordered tool list. Styled distinctly from user/assistant bubbles — left-aligned, dimmer, small routing icon. Permanent in transcript as a record.

### `DelegationOverlay`
Transient floating panel below the header (same slot as the existing pause banner). Visible only while delegation is active. Shows agent name prominently plus a scrolling live list of tool calls appending in real time: `→ bash`, `→ read_file`, etc. Fades out on completion, leaving no persistent presence. This is the "something is happening right now" signal.

### `useAmplifierEvents` extension
Gains `delegationState` and `completedDelegation` snapshot; calls `onDelegationStart` / `onDelegationEnd` callbacks on the root component to wire both the overlay and the transcript entry injection.

### Vocal injection
~10 lines in the existing `handleDataChannelEvent` in `useChatMessages`. Detects `item.name === 'delegate'`, extracts agent name, sends `conversation.item.create` + `response.create` before POST fires.

## Data Flow

1. User speaks → model calls `delegate(instruction, agent="filesystem-agent")`
2. `response.output_item.done` fires in browser with function call item
3. Browser extracts agent name from call arguments, then fires concurrently:
   - **Vocal:** sends `conversation.item.create` (acknowledgement text) + `response.create` via data channel → model speaks *"Delegating to filesystem agent now..."*
   - **Tool:** fires `POST /apps/voice/tools/execute` — blocks until sub-agent completes
4. Server spawns child session. Forwarding hook (registered at session creation, voice-only) pushes child events to parent SSE stream with `delegating_agent` tagged
5. SSE events arrive at `useAmplifierEvents`:
   - `session_fork` → `DelegationTranscriptEntry` injected (animated), `DelegationOverlay` appears
   - `tool_call { delegating_agent: "filesystem-agent", tool_name: "bash" }` → appended to overlay list
   - Further tool calls append in order
   - `delegate:agent_completed` → overlay fades, transcript entry collapses to summary with tool count and elapsed time
6. POST returns result string
7. Browser sends `function_call_output` via data channel → `response.create` → model narrates result

**Note:** Vocal injection and POST fire concurrently. Since delegations typically take several seconds, acknowledgement audio completes well before the result arrives.

## Error Handling

**Delegation fails mid-execution:** `delegate:agent_completed` still fires with an error flag. Transcript entry collapses to error summary styled in red/amber (consistent with existing error banner conventions). Overlay fades normally. Voice model narrates failure naturally via the returned tool result string.

**SSE stream drops during delegation:** If `delegate:agent_completed` never arrives but the POST to `/tools/execute` returns, the browser treats the POST return as the completion signal. Overlay and transcript entry collapse to a partial summary based on whatever tool calls were captured before the drop. Partial is better than stuck-open.

**Agent name unparseable from function call arguments:** Fall back to generic `"Delegating now..."` / `"Sub-agent"` — never block tool execution over a display concern.

**Vocal injection failure:** Fire-and-forget. If the data channel rejects the injection, log and proceed with the POST regardless.

## Testing Strategy

**Server-side event bubbling:** Additive test in the existing `test_spawn_registration.py`. Pass a mock event forwarder into `register_spawning()`, spawn a child session, emit a synthetic `tool_call` from the child, assert the forwarder was called with `delegating_agent` set. Pure Python, no WebRTC, no OpenAI — fits the existing test pattern exactly.

**Frontend state and UI components:** No new automated test infrastructure. The voice app's architecture (inline Preact, WebRTC-dependent, no existing frontend test harness) makes frontend unit testing impractical. Verification is manual.

**Manual verification checklist for PR:**
- Trigger a real delegation; confirm overlay appears with agent name and populates with tool calls
- Confirm overlay fades and transcript entry persists as compact summary on completion
- Confirm acknowledgement audio plays before tool result arrives
- Confirm SSE disconnect mid-delegation doesn't block tool execution
- Confirm fast sub-agent (0 tool calls) renders `Delegated to X · 0 tools · 0.3s` correctly

**Chat and Slack regression:** Existing tests pass unchanged — new `event_forwarder` parameter defaults to `None`, so all existing call sites are unaffected.

## Open Questions

- Exact data channel message sequence for vocal injection (timing of `response.create` relative to function call state machine) — to be validated during implementation
- Whether `DelegationOverlay` should handle concurrent delegations (currently assumed not to occur given `exclude_tools=["delegate"]` on child sessions)
- Visual styling details for `DelegationTranscriptEntry` — to be decided during implementation consistent with existing transcript bubble styles
