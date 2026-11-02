# Self-hosting on a Linux machine

A long-running copy of the server on a Linux machine you control, reachable by claude.ai and other remote clients behind Google sign-in. This is how the maintainer's copy runs since 2026-11-01 (spec 6.9, amended); [deploy.md](deploy.md) is the Cloud Run alternative. The examples use `<host>` for the public hostname, `<port>` for the local port and `<you@example.com>` for the allowlisted account.

```
client ─HTTPS─▶ tunnel or reverse proxy  https://<host>
                  └▶ ttc-mcp  127.0.0.1:<port>/mcp   Google sign-in + email allowlist, in-process
                       └▶ the TTC's and the City's servers, fetched on demand
```

Two systemd user units do the work: the HTTP server, and a daily timer that runs `ttc-mcp-build` the way the Cloud Run Job does. The server never builds (`TTC_MCP_SKIP_REFRESH=1`); when a build replaces the timetable it lands by rename and the server reopens it on its next request. An idle server makes no requests (decision g).

## 1. Install

```bash
git clone https://github.com/LunaParker/ttc-mcp.git ~/ttc-mcp
cd ~/ttc-mcp
uv sync --frozen --no-dev
.venv/bin/python -c "import sqlite3; print(sqlite3.threadsafety)"
```

The last line must print `3` (each thread gets its own read-only connection). The `cloud` extra isn't needed: no bucket and no Firestore.

## 2. A Google OAuth client

In the Google Auth Platform of any project with a consent screen, create a **Web application** client with the authorized redirect URI `https://<host>/auth/callback`. Note the client ID; the secret goes in a file next.

## 3. Secrets, one file each

```bash
install -d -m 700 ~/.config/ttc-mcp
openssl rand -hex 32 > ~/.config/ttc-mcp/jwt-signing-key
read -rs s && printf '%s' "$s" > ~/.config/ttc-mcp/google-client-secret; unset s
chmod 600 ~/.config/ttc-mcp/*
```

`GOOGLE_OAUTH_CLIENT_SECRET_FILE` and `JWT_SIGNING_KEY_FILE` name these files, so the environment file below holds no secret (a variable and its `_FILE` form can't both be set). The JWT key also derives the key that encrypts the OAuth state; replacing it signs every client out, and they register again on their next connect.

## 4. The environment file

`~/.config/ttc-mcp/server.env`:

```
TTC_MCP_CACHE=/home/<user>/.cache/ttc-mcp
TTC_MCP_SKIP_REFRESH=1
FASTMCP_HOME=/home/<user>/.local/state/ttc-mcp
BASE_URL=https://<host>
ALLOWED_EMAILS=<you@example.com>
GOOGLE_OAUTH_CLIENT_ID=<client id>
GOOGLE_OAUTH_CLIENT_SECRET_FILE=/home/<user>/.config/ttc-mcp/google-client-secret
JWT_SIGNING_KEY_FILE=/home/<user>/.config/ttc-mcp/jwt-signing-key
```

Without a Firestore project the gate keeps its OAuth state (client registrations, tokens) in FastMCP's Fernet-encrypted file store under `FASTMCP_HOME`, so a restart doesn't sign claude.ai out. Set `TTC_MCP_UA` to your own contact if you run a public copy.

## 5. The units

`~/.config/systemd/user/ttc-mcp.service`:

```ini
[Unit]
Description=ttc-mcp, Streamable HTTP on 127.0.0.1:<port>

[Service]
WorkingDirectory=%h/ttc-mcp
EnvironmentFile=%h/.config/ttc-mcp/server.env
ExecStart=%h/ttc-mcp/.venv/bin/ttc-mcp --transport http --host 127.0.0.1 --port <port>
Restart=on-failure
RestartSec=10
NoNewPrivileges=yes
UMask=0077

[Install]
WantedBy=default.target
```

Bind to loopback: `--host` defaults to `0.0.0.0`, and only the tunnel should reach the port.

`~/.config/systemd/user/ttc-mcp-build.service`:

```ini
[Unit]
Description=ttc-mcp static store: CKAN ETag check, rebuild on change

[Service]
Type=oneshot
WorkingDirectory=%h/ttc-mcp
EnvironmentFile=%h/.config/ttc-mcp/server.env
ExecStart=%h/ttc-mcp/.venv/bin/ttc-mcp-build
Nice=10
NoNewPrivileges=yes
UMask=0077
```

`~/.config/systemd/user/ttc-mcp-build.timer`:

```ini
[Unit]
Description=Daily ttc-mcp static store check at 04:30 Toronto

[Timer]
OnCalendar=*-*-* 04:30:00 America/Toronto
Persistent=true

[Install]
WantedBy=timers.target
```

Then build the first timetable by hand (about a minute) and start everything:

```bash
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user start ttc-mcp-build.service
journalctl --user -u ttc-mcp-build -n 30 --no-pager
systemctl --user enable --now ttc-mcp-build.timer ttc-mcp.service
```

Linger keeps the user units running without a login session. `Persistent=true` runs a missed build when the machine comes back. The build log ends with the `[check]` lines and `[refresh] ttc.ca service levels: N of M routes`.

## 6. Publishing it: a Cloudflare Tunnel

Any HTTPS reverse proxy or tunnel works if it forwards to `127.0.0.1:<port>` on port 443 and passes the `Authorization` header through. The maintainer's copy uses a remotely managed Cloudflare Tunnel; `<host>` must be a first-level name in the zone (`ttc-mcp.example.com`), because Universal SSL doesn't cover deeper ones without Advanced Certificate Manager.

1. Install `cloudflared` from Cloudflare's package repository (pkg.cloudflare.com).
2. Create the tunnel in Zero Trust (Networks, Tunnels) or through the API with `config_src: cloudflare`, and add a public hostname `<host>` → `http://127.0.0.1:<port>`; the catch-all rule answers `http_status:404`. A proxied CNAME `<host>` → `<tunnel id>.cfargotunnel.com` goes with it.
3. Save the tunnel token to `~/.config/ttc-mcp/tunnel-token` (mode 0600) and run it as a third user unit:

```ini
[Unit]
Description=Cloudflare Tunnel: <host> to 127.0.0.1:<port>

[Service]
ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate run --token-file %h/.config/ttc-mcp/tunnel-token
Restart=on-failure
RestartSec=10
NoNewPrivileges=yes
UMask=0077

[Install]
WantedBy=default.target
```

Its journal shows four "Registered tunnel connection" lines once it is up. Tailscale Funnel also works, but only on port 443 for claude.ai: on 2026-11-01 claude.ai never reached a Funnel on port 8443 although `curl` through the same ingress did (spec section 11).

## 7. Checks

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:<port>/.well-known/oauth-authorization-server   # 200
curl -s https://<host>/.well-known/oauth-protected-resource/mcp                                            # 200, resource https://<host>/mcp
curl -s -D - -o /dev/null -X POST -H 'Content-Type: application/json' -d '{}' https://<host>/mcp             # 401 with www-authenticate: Bearer ... resource_metadata=...
```

Then connect a client: in claude.ai, Customize, Connectors, Add custom connector, `https://<host>/mcp`. It detects "Sign in now" and Claude's published client identity; Connect leads through the gate's consent page and Google sign-in with an allowlisted account. Ask "What is the TTC service status right now?" and `service_status` answers.

## 8. Updating

```bash
cd ~/ttc-mcp && git pull --ff-only && uv sync --frozen --no-dev
systemctl --user restart ttc-mcp
```

When a release bumps the timetable schema the server skips the old file until the next build, so start `ttc-mcp-build.service` by hand right after.

## On WSL

A WSL2 distro with systemd enabled runs all of this, but WSL stops a distro a few seconds after its last client exits, linger or not. Keep one detached client alive from Windows, for example a scheduled task with a boot trigger that runs `wsl.exe -d <distro> --exec sleep infinity` hidden under an S4U principal, so the distro and its units come back after an unattended reboot. A loopback bind and an outbound tunnel need no port forwarding and no mirrored networking.
