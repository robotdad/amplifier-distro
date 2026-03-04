# Secure Remote Access Design

## Goal

Address GitHub issue #68: voice app fails on remote access because `getUserMedia` requires HTTPS. The fix encompasses four independent layers — browser-side secure context detection, native TLS support, PAM authentication for remote access, and a CSRF origin fix.

## Background

amplifier-distro is a FastAPI/Python web server hosting multiple AI front-end apps (Chat, Voice, Slack, Routines, Settings). The server binds to `0.0.0.0:8400` by default, making remote access common.

The voice app requires `navigator.mediaDevices.getUserMedia`, which browsers restrict to secure contexts (HTTPS or localhost). When a user accesses the server remotely over plain HTTP, `navigator.mediaDevices` is `undefined`, producing a cryptic TypeError with no guidance on what went wrong.

Current state:
- Auth is opt-in bearer token via `AMPLIFIER_SERVER_API_KEY` — machine-to-machine only, no browser login flow
- `tailscale.py` already integrates with `tailscale serve` as a reverse proxy option
- Target audience for remote access: advanced users running headless Linux servers, likely already have Tailscale
- Most users are on laptops accessing localhost — their experience must remain unchanged

## Approach

Four independent, composable layers that each deliver value on their own. They can be implemented and shipped incrementally. Plain `amp-distro serve` with no configuration behaves exactly as it does today — zero breaking changes.

## Architecture

```
Browser                          Server (FastAPI/Uvicorn)
  │                                │
  │  1. Secure Context Guard       │
  │  (JS: isSecureContext check)   │
  │                                │
  │  ──── HTTPS (Layer 2) ────►   │  2. Native TLS (uvicorn ssl)
  │                                │     - Tailscale cert or self-signed
  │                                │
  │  ──── Cookie / Token ────►    │  3. PAM Auth (Linux/WSL + TLS only)
  │                                │     - Session cookie for browsers
  │                                │     - Bearer token for API clients
  │                                │
  │  ──── Origin header ────►     │  4. CSRF Origin allow-list
  │                                │     - Dynamic, built at startup
```

## Components

### Layer 1: Browser-Side Secure Context Guard

**Scope:** All platforms. Pure frontend, zero server changes.

In the voice app's `connect()` function, before calling `navigator.mediaDevices.getUserMedia()`, check `window.isSecureContext`. If false, display a message in the voice UI itself (not console, not alert):

> **Voice requires a secure connection**
> Your browser can't access the microphone over plain HTTP. To use voice remotely:
> - Run with `amp-distro serve --tls auto` to enable HTTPS
> - Or access via `https://your-tailscale-hostname`
> - Voice works on localhost without any setup

This replaces the current behavior where `navigator.mediaDevices` is `undefined` and a cryptic TypeError is thrown with no guidance.

### Layer 2: Native TLS in Uvicorn

**Scope:** All platforms. Server-side only.

The server gains the ability to serve HTTPS directly via uvicorn's ssl support.

**CLI surface:**
- `amp-distro serve --tls auto` — auto-provision cert (Tailscale → self-signed fallback)
- `amp-distro serve --ssl-certfile /path --ssl-keyfile /path` — manual cert
- `amp-distro serve` — no TLS, plain HTTP, unchanged from today

**Auto-provisioning priority (`--tls auto`):**
1. Tailscale available and logged in → run `tailscale cert <hostname>`, write cert/key to `~/.amplifier-distro/certs/`
2. No Tailscale → generate self-signed cert using Python `ssl` stdlib, write to same directory, log clear warning

**Key decisions:**
- TLS is opt-in, not automatic. Plain `amp-distro serve` stays HTTP.
- Certs stored in `~/.amplifier-distro/certs/` (excluded from backup)
- Tailscale certs re-provisioned on startup if expired or near expiry
- Self-signed certs generated once and reused (browser trust click-through happens once)
- Existing `tailscale.py` reverse proxy mode remains as an alternative path
- Default port (8400) and bind address (0.0.0.0) unchanged

### Layer 3: PAM Authentication

**Scope:** Linux/WSL only. Activates when the server is serving over HTTPS **and** the platform is Linux/WSL. Otherwise auth is skipped entirely — open access as today.

**Login flow** (following the [filebrowser](https://github.com/robotdad/filebrowser) pattern):
1. User navigates to any page → server checks for a valid session cookie
2. No cookie or expired → redirect to `/login`
3. User enters Linux username and password
4. Server calls `python-pam` to verify against the OS → on success, issues an `itsdangerous` TimestampSigner cookie (HttpOnly, Secure, SameSite=Strict)
5. Subsequent requests carry the cookie → `require_auth` dependency validates it
6. Logout deletes the cookie client-side

**Key decisions:**
- **Localhost bypasses auth entirely** — `http://localhost:8400` needs no login, same as today
- **Session timeout:** configurable, 30 days default (personal device, stay logged in)
- **Secret key:** auto-generated on first run, stored in `~/.amplifier-distro/`
- **Shadow group requirement:** documented clearly; `amp-distro doctor` checks for it and tells the user how to fix it
- **Failure logging:** log `pam.reason` server-side so shadow group misconfiguration is diagnosable (never exposed to client) — unlike filebrowser, makes common misconfiguration easy to debug
- **Existing `AMPLIFIER_SERVER_API_KEY`:** continues to work as-is for programmatic/API access. Two auth mechanisms coexist — cookie for browsers, bearer token for API clients
- **WebSocket auth:** session cookie is sent on the initial HTTP upgrade handshake, so WebSocket auth works automatically — no special handling needed
- **macOS:** PAM skipped entirely. macOS users are the laptop/localhost use case. macOS server support deferred to future work.
- **Dependencies:** `python-pam`, `itsdangerous` (both proven in filebrowser)

### Layer 4: CSRF Origin Fix

**Scope:** All platforms.

Replace the hardcoded `localhost`/`127.0.0.1` check in the voice app's `_check_origin()` with a dynamic allow-list built at startup:

- Always allow `localhost` and `127.0.0.1` (as today)
- If Tailscale is active, add the Tailscale DNS name (e.g., `monad.tail-abc.ts.net`)
- Add the server's actual hostname (from `socket.gethostname()`)
- Add any explicitly configured origins from settings (escape hatch for unusual setups)

This lives in a shared utility that any app can use — not just voice. The chat app's WebSocket endpoints and any future apps that need origin checking all use the same allow-list.

**No wildcard, no `*`** — enumerate known-good origins only. If someone has an unanticipated setup, the settings config lets them add their own.

## Configuration & Settings

**Settings in `~/.amplifier-distro/settings.yaml`:**

```yaml
server:
  tls:
    mode: "auto"                    # "auto" | "off" | "manual"
    certfile: null                   # path for manual mode
    keyfile: null                    # path for manual mode
  auth:
    enabled: true                    # enable PAM auth (Linux/WSL only, requires TLS)
    session_timeout: 2592000         # 30 days in seconds
  allowed_origins:                   # additional CSRF origins (escape hatch)
    - "https://my-custom-proxy.local"
```

**CLI flags override settings** for one-off use:
- `--tls auto|off|manual`
- `--ssl-certfile` / `--ssl-keyfile` (implies `--tls manual`)
- `--no-auth` (disable PAM even when TLS is active)

**Defaults:** TLS off, auth enabled (but only activates when TLS is on + Linux). `amp-distro serve` with no config behaves exactly as today — zero breaking changes.

**`amp-distro doctor` gains new checks:**
- Is the user in the `shadow` group? (Linux only)
- Are certs present and valid? (if TLS configured)
- Is Tailscale available? (informational)

## Error Handling

- **TLS auto-provisioning failure:** If Tailscale cert fails and self-signed generation also fails, server starts without TLS and logs a clear warning explaining why
- **PAM auth failure:** `pam.reason` logged server-side for diagnostics; client sees generic "authentication failed" — never leaks PAM internals
- **Shadow group misconfiguration:** `amp-distro doctor` detects and provides fix instructions; PAM failure logs include the specific `pam.reason` to make this diagnosable without doctor
- **Expired certs:** Tailscale certs re-provisioned on startup; self-signed certs are long-lived and regenerated if invalid
- **Missing secure context:** Voice UI shows actionable message instead of crashing with TypeError

## Testing Strategy

- **Layer 1:** Manual browser testing — verify the secure context guard displays correctly on HTTP and is absent on HTTPS/localhost
- **Layer 2:** Integration tests for TLS startup — verify uvicorn accepts connections over HTTPS with both Tailscale and self-signed certs; unit tests for cert provisioning logic
- **Layer 3:** Unit tests for PAM auth flow with mocked `python-pam`; integration test for cookie lifecycle (login → authenticated request → logout); verify localhost bypass; verify bearer token still works alongside cookie auth
- **Layer 4:** Unit tests for dynamic origin allow-list construction; verify origins are correctly built from Tailscale status, hostname, and config

## Open Questions

- Should the settings UI surface TLS/auth configuration, or is CLI + config file sufficient for the target audience?
- Token revocation: current design is cookie-delete only (like filebrowser). Should we add server-side session invalidation?
- Self-signed cert UX: some mobile browsers make it very hard to trust self-signed certs. Is this acceptable as a fallback or do we need to warn more aggressively?

## References

- GitHub issue: https://github.com/microsoft/amplifier-distro/issues/68
- PAM pattern reference: https://github.com/robotdad/filebrowser
- Branch: `feat/secure-remote-access-68`
