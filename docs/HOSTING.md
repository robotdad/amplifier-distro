# Hosting amplifier-distro

This guide covers how to run `amp-distro` in four common deployment scenarios. The right
choice depends on whether you need remote access and whether another service is already
handling TLS and authentication for you.

For deeper technical details on TLS modes, proxy trust, and cookie configuration, see the
[amplifierd HOSTING.md](https://github.com/microsoft/amplifierd/blob/main/docs/HOSTING.md).

---

## Smart Defaults Based on `--host`

`amp-distro` infers your intent from the `--host` flag and applies secure defaults
automatically. You should not need to configure TLS or auth manually for the most common
scenarios.

| Command | Host | TLS | Auth | Warning |
|---------|------|-----|------|---------|
| `amp-distro` | `127.0.0.1` | off | off | — |
| `amp-distro --host 0.0.0.0` | `0.0.0.0` | auto | enabled | — |
| `amp-distro --host 0.0.0.0 --tls off` | `0.0.0.0` | off | enabled | printed to stderr |

**Rule:** If you bind to a non-localhost address, TLS defaults to `auto` and authentication
defaults to enabled. Pass `--no-auth` or `--tls off` to override explicitly.

---

## Deployment Scenarios

### 1. Local Development

**Use this when:** working on your own machine (laptop, desktop, WSL2).

```bash
amp-distro
```

| Setting | Value |
|---------|-------|
| Bind address | `127.0.0.1:8410` |
| TLS | off |
| Auth | off |

The server only accepts connections from the same machine. No TLS, no API key, no cookies —
the OS network stack is the security boundary. Open [http://localhost:8410](http://localhost:8410)
to begin.

---

### 2. Behind a Reverse Proxy (Same Machine)

**Use this when:** a proxy (Caddy, nginx, frontdoor) on the same host terminates TLS and
handles authentication, forwarding requests to amplifier-distro on localhost.

```bash
amp-distro
```

The command is identical to local development — distro stays on loopback and lets the proxy
handle everything else. Configure the proxy to inject `X-Authenticated-User` and forward
requests to `http://127.0.0.1:8410`.

**Enable proxy auth trust** by adding this to `~/.amplifier/.env`:

```env
AMPLIFIERD_TRUST_PROXY_AUTH=true
```

When this is set, amplifierd reads the `X-Authenticated-User` header from requests arriving
from trusted proxies (localhost by default) and treats its value as the authenticated username.
No additional auth configuration is needed in distro.

| Setting | Value |
|---------|-------|
| Bind address | `127.0.0.1:8410` |
| TLS | off (proxy terminates) |
| Auth | delegated to proxy via `X-Authenticated-User` |
| `~/.amplifier/.env` | `AMPLIFIERD_TRUST_PROXY_AUTH=true` |

> **Example nginx snippet:**
> ```nginx
> location / {
>     proxy_pass http://127.0.0.1:8410;
>     proxy_set_header X-Authenticated-User $auth_user;
>     proxy_set_header X-Forwarded-For $remote_addr;
>     proxy_set_header X-Forwarded-Proto $scheme;
> }
> ```

---

### 3. Standalone Remote Server

**Use this when:** running on a headless server that you access over the network, and
distro is the only web service on that machine managing its own TLS and auth.

```bash
amp-distro --host 0.0.0.0
```

Binding to `0.0.0.0` triggers smart defaults automatically:

- **TLS:** `auto` — probes for Tailscale certificates first, falls back to a self-signed
  certificate if Tailscale is absent or the cert request fails.
- **Auth:** enabled — amplifierd requires authentication via PAM login or API key.

| Setting | Value |
|---------|-------|
| Bind address | `0.0.0.0:8410` |
| TLS | auto (Tailscale → self-signed fallback) |
| Auth | enabled |
| Cookie secure | `true` (auto, follows TLS) |
| Cookie SameSite | `lax` |

Access the server at `https://<your-host>:8410`. If using Tailscale, the Tailscale hostname
resolves automatically and the certificate is trusted. With a self-signed certificate, your
browser will show a security warning — accept it for your personal server.

#### Manual TLS (Bring Your Own Certificates)

If you have existing certificates (e.g., from Let's Encrypt or your own CA), you can pass
them directly instead of relying on auto-provisioning:

```bash
# Using your own certificates
amp-distro --host 0.0.0.0 --tls manual --ssl-certfile /path/to/cert.pem --ssl-keyfile /path/to/key.pem
```

---

### 4. Behind a Remote Proxy (Different Machine)

**Use this when:** a reverse proxy on a **different host** handles TLS termination and
authentication before forwarding traffic to your distro server.

```bash
amp-distro --host 0.0.0.0 --tls off
```

> **Warning:** Running on a network interface without TLS. Only use this configuration behind
> a trusted reverse proxy that terminates TLS externally.

Because the proxy is on a different machine, you must explicitly configure which proxy IPs
to trust. Add to `~/.amplifier/.env`:

```env
AMPLIFIERD_TRUST_PROXY_AUTH=true
AMPLIFIERD_TRUSTED_PROXIES=10.0.0.1
```

Replace `10.0.0.1` with your proxy's IP address. Without an explicit `AMPLIFIERD_TRUSTED_PROXIES`,
amplifierd logs a warning at startup because the default (localhost only) will not match
requests forwarded from a remote proxy.

| Setting | Value |
|---------|-------|
| Bind address | `0.0.0.0:8410` |
| TLS | off (proxy handles TLS) |
| Auth | delegated to proxy via `X-Authenticated-User` |
| `~/.amplifier/.env` | `AMPLIFIERD_TRUST_PROXY_AUTH=true`, `AMPLIFIERD_TRUSTED_PROXIES` |

---

## Service Installation

`amp-distro service install` registers a platform service (systemd on Linux, launchd on
macOS) that starts distro automatically at boot.

### Install Variants

**Local development (default):** Start on localhost, no TLS, no auth. Suitable for
laptops and personal machines.

```bash
amp-distro service install
# ExecStart: amp-distro --host 127.0.0.1 --port 8410
```

**Standalone remote server:** Start on all interfaces with TLS auto and auth enabled.

```bash
amp-distro service install --host 0.0.0.0
# ExecStart: amp-distro --host 0.0.0.0 --port 8410
# (TLS=auto and auth=enabled applied at runtime via smart defaults)
```

**Behind a remote proxy:** Start on all interfaces with TLS disabled, relying on the
proxy for TLS termination.

```bash
amp-distro service install --host 0.0.0.0 --tls off
# ExecStart: amp-distro --host 0.0.0.0 --tls off --port 8410
```

### Upgrading Existing Services

Check the status of the installed service:

```bash
amp-distro service status
```

If `amp-distro service status` shows a warning about a **stale unit** referencing an
old binary or a removed subcommand, uninstall and reinstall the service to migrate:

```bash
amp-distro service uninstall
amp-distro service install [--host <host>] [--tls <mode>]
```

This is expected when upgrading across major CLI changes (e.g., the `serve` subcommand
was removed in favour of bare `amp-distro`).

---

## Service Environment Overrides

The service unit loads `~/.amplifier/.env` as an `EnvironmentFile` before starting the
server. You can use this file to pass environment overrides without modifying the service
unit itself.

**Example `~/.amplifier/.env`:**

```env
# Proxy auth trust (see scenarios 2 and 4)
AMPLIFIERD_TRUST_PROXY_AUTH=true

# Trusted proxy IPs (required for remote proxies)
AMPLIFIERD_TRUSTED_PROXIES=10.0.0.1

# Override TLS mode
AMPLIFIERD_TLS_MODE=off

# Override cookie behavior
AMPLIFIERD_COOKIE_SAMESITE=lax
```

Changes to `~/.amplifier/.env` take effect on the next service restart. No need to
reinstall the service unit or run `daemon-reload`.

---

## Smart Defaults Reference

Full mapping of flag combinations to runtime behavior:

| `--host` | `--tls` | `--no-auth` | Effective TLS | Effective Auth | Warning |
|----------|---------|-------------|---------------|----------------|---------|
| _(default)_ | _(default)_ | — | off | off | — |
| _(default)_ | `off` | — | off | off | — |
| _(default)_ | `auto` | — | auto | off | — |
| `0.0.0.0` | _(default)_ | — | auto | enabled | — |
| `0.0.0.0` | `auto` | — | auto | enabled | — |
| `0.0.0.0` | `off` | — | off | enabled | printed to stderr |
| `0.0.0.0` | _(default)_ | `--no-auth` | auto | off | — |
| `0.0.0.0` | `off` | `--no-auth` | off | off | printed to stderr |

**TLS `auto` provisioning chain:**

1. Probe for Tailscale certificate via `tailscale cert` — uses if available.
2. Generate a self-signed certificate — uses as fallback.
3. If both fail, falls back to no TLS with a clear error message.

---

## Rationale

### Why `samesite=lax` as the default?

`strict` blocks cookies on **all** cross-site navigations — clicking a link from Slack,
an email, or a bookmark forces a re-login even though the user is authenticated. `lax`
allows cookies on top-level navigations (clicking a link) but blocks cross-site subrequest
cookies, protecting against CSRF while keeping normal link navigation functional.

`none` requires `Secure=true` and opens CSRF risk. `lax` is the broadly accepted default
for web application cookies.

### Why centralized TLS at the proxy is better for multi-service hosts?

A single reverse proxy (Caddy, nginx, frontdoor) managing one certificate is simpler than
each service managing its own: one renewal process, one configuration to update, one
place to rotate credentials. Applications bind to localhost, invisible to the network,
and the proxy handles everything external.

This is why `amp-distro` defaults to TLS-off on localhost — it expects a proxy to exist on
multi-service hosts, and avoids creating a parallel TLS infrastructure.

### Why self-signed is the right fallback after Tailscale?

An encrypted connection with a click-through browser warning is strictly better than an
unencrypted connection. Self-signed certificates protect against passive eavesdropping on
the path between client and server, even though they do not provide CA-signed identity
verification.

For a personal server accessed by one person who controls both ends, a self-signed
certificate is a reasonable security posture.

### Why is Tailscale HTTP (without TLS) acceptable in some configurations?

Tailscale uses WireGuard under the hood, which encrypts all traffic in transit between
Tailscale nodes. Traffic routed through the Tailscale network is already encrypted at the
network layer, making application-level TLS redundant for intra-network communication.

That said, HTTPS is still preferred even over Tailscale: it enables browser `Secure`
cookies, triggers browser security features, and avoids confusing browser warnings when
accessing the server by hostname.

---

## Further Reading

- **[amplifierd HOSTING.md](https://github.com/microsoft/amplifierd/blob/main/docs/HOSTING.md)** — Technical reference for
  TLS modes, proxy trust configuration, cookie behavior, and port auto-increment. Covers
  the amplifierd settings that `amp-distro` passes through.
