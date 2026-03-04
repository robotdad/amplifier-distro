# Session History Sidebar Performance

**Date:** 2026-03-10
**Status:** Approved
**Scope:** `distro-server/src/amplifier_distro/server/apps/chat/session_history.py`

## Problem

Loading 300 sessions takes 5s+ because `_read_session_meta` opens and JSON-parses
every line of every `transcript.jsonl`. The sidebar shows a spinner until the
entire scan completes.

## Solution

Eliminate transcript reads. `metadata.json` already has `turn_count` (written by
`MetadataSaveHook` on every orchestrator:complete). The only field requiring a
transcript read is `last_user_message`, which can be extracted by reading the last
8KB of the file instead of the whole thing. Parallelize all session reads across
threads.

## Changes (single file: `session_history.py`)

No new endpoints. No frontend changes. No new modules. Same API contract.

### 1. Read `turn_count` from `metadata.json`

The code already reads `metadata.json` for name, description, parent_id,
spawn_agent. Add `turn_count` to what it extracts.

Fallback: if `turn_count` is missing (old sessions pre-hook), `message_count`
stays 0. The transcript tail-read below will still detect content.

### 2. Replace full transcript scan with seek-from-end

New helper `_read_last_user_message(transcript_path)`:
- Seek to `max(0, file_size - 8192)` (last 8KB)
- Read chunk, split lines, iterate in reverse
- Find last line with `role == "user"`, return `content[:120]`
- On any error or not found, return None

Why 8KB: a typical transcript line is 200-500 bytes. 8KB covers ~15-40 lines.
The last user message is almost always in the final few lines.

Replace the transcript full-scan block with:
- `last_user_message = _read_last_user_message(transcript_path)`
- If metadata had no `turn_count` and transcript is non-empty, set
  `message_count = 1` (passes empty-session filter)

### 3. Parallelize session reads

Replace sequential for-loop in `scan_sessions` with
`ThreadPoolExecutor(max_workers=8)` using `pool.map()`.

Each session read is I/O-bound (stat, open, read small files). 8 threads
lets the OS pipeline disk reads.

## Per-session I/O comparison

| Operation | Before | After |
|---|---|---|
| stat(transcript.jsonl) | 1 call | 1 call |
| read(session-info.json) | 1 read | 1 read |
| read(metadata.json) | 1 read | 1 read (extract more fields) |
| read(transcript.jsonl) | Full file, every line, JSON parse each | Last 8KB, ~5-10 JSON parses |
| Parallelism | Sequential | 8 threads |

## Edge cases

| Case | Behavior |
|---|---|
| Old session without turn_count in metadata | message_count = 1 if transcript non-empty |
| Transcript < 8KB | Reads entire file (seek lands at 0) |
| Last user message not in last 8KB | Returns None, sidebar shows name only |
| Corrupted transcript | JSONDecodeError caught per-line, skipped |
| Missing transcript file | message_count from metadata, last_user_message = None |
| Thread pool exception | Caught per-session, session skipped with warning |

## What is NOT changing

- No new API endpoints
- No frontend changes
- No new files or modules
- API response shape stays identical
- `scan_session_revisions` unchanged (already doesn't read transcripts)
- Active session sidebar updates via WebSocket (unaffected)

## Data sources for sidebar fields

| Field | Source | Transcript needed? |
|---|---|---|
| session_id | Directory name | No |
| cwd | session-info.json | No |
| name | metadata.json | No |
| description | metadata.json | No |
| parent_session_id | metadata.json | No |
| spawn_agent | metadata.json | No |
| last_updated | stat(transcript.jsonl) | No |
| revision | stat(transcript.jsonl) | No |
| turn_count (message_count) | metadata.json | No |
| last_user_message | transcript.jsonl tail 8KB | Yes (minimal) |

## turn_count provenance

Written by both entry points:
- **distro-server:** `MetadataSaveHook` in `metadata_persistence.py` fires on
  `orchestrator:complete`, counts `role == "user"` messages
- **CLI:** `IncrementalSaveHook` in `incremental_save.py` fires on `tool:post`,
  same counting logic

Both write to `metadata.json` in the session directory.