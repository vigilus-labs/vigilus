# Vigilus

**Self-hosted, AI-powered homelab operations platform.**

> ⚠️ **Status: Beta.** Vigilus is under active development and still has rough
> edges. Things may break, and some features are incomplete. If you run into a
> bug, a crash, or behavior that surprises you, please
> [open an issue](https://github.com/vigilus-labs/vigilus/issues) — reports and
> feedback are what make it better.

A web dashboard + conversational AI + MCP server manager + user-definable agent ("Operator") system, in one deployable app.

## Features

- **Conversational AI Orchestration** — Chat with Vigilus, the built-in orchestrator, which routes tasks to specialized Operators
- **User-Defined Operators** — Create sub-agents with custom system prompts, models, tools, and permission levels
- **MCP Server Management** — Register, start/stop, and manage Model Context Protocol servers; automatically discover and assign their tools
- **RBAC + JIT Access** — Fine-grained permission levels (read/write/exec/elevate) with Just-In-Time access elevation for sensitive operations
- **Full Audit Trail** — Every tool call logged, secrets redacted, append-only action history with export
- **Scheduled Tasks** — Create recurring tasks in the UI (cron-based, with presets); each run is sent to the orchestrator and produces a reviewable chat session
- **Server Inventory** — Manage homelab hosts with encrypted SSH credentials and connectivity testing
- **Multi-Provider LLM Support** — Anthropic, OpenAI, Google, Ollama, LM Studio, vLLM, OpenRouter, xAI, and any OpenAI-compatible endpoint
- **Real-Time Dashboard** — WebSocket-driven live updates across the entire UI

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend build)
- An LLM API key (Anthropic, OpenAI, or local model endpoint)

### Bare Metal

```bash
# Clone the repository
git clone https://github.com/vigilus-labs/vigilus.git
cd vigilus

# Backend setup
cd backend
pip install -e ".[dev]"
export VIGILUS_SECRET="$(openssl rand -hex 32)"
vigilus init
vigilus start

# Frontend (development)
cd ../frontend
npm install
npm run dev
```

### Docker (prebuilt image)

Published to GHCR on every release and `main` build. `VIGILUS_SECRET` is
required — generate a stable one and keep it (it encrypts stored API keys and
SSH credentials, so changing it makes existing data unreadable):

```bash
docker run -d --name vigilus \
  -p 8000:8000 \
  -e VIGILUS_SECRET="$(openssl rand -hex 32)" \
  -v vigilus-data:/app/data \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  ghcr.io/vigilus-labs/vigilus:latest
```

Access the UI at `http://localhost:8000`. Pin a version with
`ghcr.io/vigilus-labs/vigilus:vX.Y.Z` instead of `:latest`.

### Docker (build from source)

`docker/docker-compose.yml` requires `VIGILUS_SECRET` at parse time (it uses
`${VIGILUS_SECRET:?...}`), so provide it via an env file in the compose
project directory:

```bash
# One-time setup: create docker/.env with a stable secret.
# Reuse the same value from backend/.env if you've run bare-metal before —
# VIGILUS_SECRET encrypts stored API keys and SSH credentials, so regenerating
# it would make existing encrypted data unreadable.
grep '^VIGILUS_SECRET=' backend/.env > docker/.env 2>/dev/null \
  || echo "VIGILUS_SECRET=$(openssl rand -hex 32)" > docker/.env

# Run with Docker Compose
docker compose -f docker/docker-compose.yml up -d
```

`docker/.env` is gitignored. To override the secret for a single run (e.g. CI):

```bash
docker compose --env-file /path/to/.env -f docker/docker-compose.yml up -d
```

Access the UI at `http://localhost:8000`.

## Configuration

All settings are configurable via environment variables with the `VIGILUS_` prefix:

| Variable | Default | Description |
|---|---|---|
| `VIGILUS_SECRET` | *(required)* | Master key for encryption + JIT signing |
| `VIGILUS_DATABASE_URL` | `sqlite+aiosqlite:///./data/vigilus.db` | Database connection string |
| `VIGILUS_DATA_DIR` | `./data` | Data directory for DB, keys, MCP working dirs |
| `VIGILUS_HOST` | `0.0.0.0` | Server bind address |
| `VIGILUS_PORT` | `8000` | Server port |
| `VIGILUS_TRUST_MODE` | `strict` | Default trust mode (`strict` or `lenient`) |
| `VIGILUS_JIT_MAX_TTL` | `60` | Maximum JIT token TTL in minutes |
| `VIGILUS_JIT_DEFAULT_TTL` | `15` | Default JIT token TTL in minutes |
| `VIGILUS_CORS` | `http://localhost:5173` | CORS origins (dev only) |
| `VIGILUS_LOG_LEVEL` | `INFO` | Log level |
| `VIGILUS_SEARCH_ENABLED` | `true` | Master switch for web research (Vigilus-only) |
| `VIGILUS_SEARCH_BACKEND` | `searxng` | Search backend (`searxng` or `firecrawl`) |
| `VIGILUS_FETCH_BACKEND` | `builtin` | Page-fetch backend (`builtin` SSRF-safe, or `firecrawl`) |
| `VIGILUS_SEARXNG_URL` | *(none)* | SearXNG base URL, e.g. `http://searxng.lan:8080` |
| `VIGILUS_FIRECRAWL_API_KEY` | *(none)* | Firecrawl key (DB config wins; encrypted at rest) |
| `VIGILUS_WEB_FETCH_ALLOW_PRIVATE` | `false` | Allow fetching internal/LAN URLs (off = SSRF-safe) |
| `VIGILUS_UPDATE_CHECK` | `true` | Check GitHub releases for a newer version (set `false` to disable the outbound call) |

> **Web research** is performed by the Vigilus orchestrator only — operators
> never get `web_search`/`web_fetch`. Configure it under **Settings → Search**
> (DB config wins over env). For **SearXNG**, the instance's `settings.yml` must
> enable the JSON format (it's off by default):
>
> ```yaml
> search:
>   formats:
>     - html
>     - json
> ```
>
> `vigilus doctor` probes the configured backend and prints this fix if it gets
> HTML back. The builtin fetcher blocks loopback/RFC1918/link-local/metadata
> addresses and re-validates every redirect.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   Frontend (React)               │
│  Dashboard │ Chat │ Operators │ MCP │ JIT │ ...  │
├─────────────────────────────────────────────────┤
│              WebSocket + REST API                │
├─────────────────────────────────────────────────┤
│                 Vigilus Orchestrator              │
│        ┌─────────┬──────────┬──────────┐        │
│        │Operator A│Operator B│Operator C│        │
│        └────┬────┴────┬─────┴────┬─────┘        │
│             │         │          │               │
│     ┌───────┴─────────┴──────────┴────────┐     │
│     │          Tool Registry (RBAC)        │     │
│     ├──────┬──────────┬───────────────────┤     │
│     │Native│   HTTP   │       MCP         │     │
│     │Tools │  Tools   │      Tools        │     │
│     └──┬───┴────┬─────┴────┬──────────────┘     │
│        │        │          │                     │
│   SSH/Docker  APIs    MCP Servers               │
│   Wazuh/Host                                    │
├─────────────────────────────────────────────────┤
│  Audit Log │ JIT Warden │ Crypto │ Event Bus    │
├─────────────────────────────────────────────────┤
│              SQLite / PostgreSQL                 │
└─────────────────────────────────────────────────┘
```

## Administration

### Updating

Installs created by `install.sh` can update themselves:

```bash
vigilus update            # pull the latest code, rebuild, migrate, restart
vigilus update --check    # just report whether an update is available
```

The update pulls the latest commit from GitHub, reinstalls backend
dependencies, rebuilds the web UI, applies database migrations, and restarts
the service (systemd or launchd). Local modifications to tracked files abort
the update unless you pass `--force`, which discards them. Your `.env` and
`data/` directory are never touched.

For Docker installs, pull the newer image and recreate the container instead.

### Password Reset

Password reset requires shell access to the server. Run the appropriate command, then log in with the new password.

**Bare metal**

```bash
vigilus user reset-password YOUR_USERNAME
```

**Docker**

```bash
docker exec -it vigilus vigilus user reset-password YOUR_USERNAME
```

The command prompts for a new password (min 10 characters). It also invalidates all existing sessions for that user, so any logged-in browsers are forced back to the login page.

### User Management (CLI)

```bash
vigilus user list                          # show all users
vigilus user create YOUR_USERNAME          # create a new user
vigilus user reset-password YOUR_USERNAME  # reset password + revoke sessions
```

The `vigilus user` commands work against the same database the server uses. The server does not need to be stopped first.

### Third-Party Channels (Telegram / Discord)

Vigilus can be reached from Telegram and Discord. The gateway runs in-process
(sharing the DB and orchestrator), bot tokens are Fernet-encrypted at rest,
and **access is default-deny** — you must explicitly allow a platform user
before the bot will talk to them.

```bash
# 1. Store a bot token (encrypted)
vigilus channels set-token --platform telegram --token "<token from @BotFather>"

# 2. Allow yourself (find your id via @userinfobot, or Discord dev mode → Copy ID)
vigilus channels allow --platform telegram --user-id <your-id> --label me

# 3. Restart Vigilus, then message the bot. Manage later with:
vigilus channels list
vigilus channels revoke --platform telegram --user-id <your-id>
```

The same can be done from **Settings → Channels** in the web UI.

**Telegram:** create a bot via [@BotFather](https://t.me/BotFather) and copy
the token. No public URL needed (long-polling). Tokens can also be supplied
via `VIGILUS_TELEGRAM_BOT_TOKEN`.

**Discord:**
1. Create an app + bot in the [Developer Portal](https://discord.com/developers/applications) and copy the bot token.
2. **Enable “Message Content Intent”** under *Bot → Privileged Gateway Intents*
   (without it the bot receives empty `content`).
3. Invite the bot with the `bot` scope and “Send Messages” permission.
4. `pip install -e '.[channels]'` (adds `discord.py`).
5. Tokens can also be supplied via `VIGILUS_DISCORD_BOT_TOKEN`.

In group chats / guilds the bot only responds when **@mentioned** unless you
toggle *Respond in groups* for that platform. Slash commands (`/help`,
`/status`, …) work the same as in the web chat.

## Security

- All secrets encrypted at rest (AES via Fernet)
- Every tool call passes through RBAC policy engine
- Permission levels are hard ceilings — JIT is the only elevation path
- Append-only audit log with secret redaction
- JIT tokens are HMAC-signed, short-lived, and revocable
- SSH host keys verified trust-on-first-use: keys are pinned to `data/known_hosts` on first connect, and connections fail if a host's key later changes
- Host tools (`shell_exec`, `fs_read`, `fs_write`, `fs_list`) are confined to the operator's working directory when one is set — including symlink and path-traversal escapes
- JIT tokens are scoped to the resource they were approved for (glob + path containment)
- CORS locked to configured origins

## Trust Modes

- **Strict**: a denied tool call **pauses the whole delegation** while a JIT approval card appears inline in the chat (and on the JIT page). Approve and the operator continues automatically; deny and it aborts that action. The wait window is `VIGILUS_JIT_WAIT_SECONDS` (default 180).
- **Lenient**: the same requests are auto-approved and execution continues immediately — still fully logged.

## License

MIT
