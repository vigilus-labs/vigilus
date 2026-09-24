# Changelog

All notable changes to Vigilus are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- SQLite now runs in WAL mode with an explicit 5s busy timeout, so the
  scheduler, gateway, JIT polling, audit writes and chat can write
  concurrently without "database is locked" errors
- Startup now applies pending Alembic migrations to an existing database
  instead of running `create_all` (which never adds new columns). If the
  schema can't be migrated, the server refuses to start with a clear message
  rather than failing later with "no such column"
- Sending another message to a chat session while a turn is still running
  now fails fast with a clear 409 instead of letting the two turns
  interleave and corrupt each other's history; the Telegram/Discord gateway
  likewise replies with a "still working" notice instead of starting a
  second concurrent turn in the same conversation

## [0.3.2] - 2026-09-15

## [0.3.1] - 2026-09-15

### Added

- In-app self-update: **Settings → About → Update now** runs the same update
  as `vigilus update` from the web UI, with live progress and automatic
  reconnect across the service restart (git-managed installs only)
- `install.sh` now installs a polkit rule so the service user can restart
  exactly its own unit — required for in-app updates to restart the service

### Fixed

- `vigilus update` failed during the frontend rebuild on system installs:
  the service user has no home directory, so npm died with EACCES creating
  its cache. Update subprocesses now keep their pip/npm caches inside the
  install tree, and `npm install` is no longer run with `--silent` (its
  errors were invisible)
- `vigilus update` refuses to run as a user other than the install's owner
  (root included) with the exact `sudo -u <owner>` command — mixing users
  corrupts file ownership mid-update
- `vigilus update` no longer reports success (exit 0) when the service
  restart fails: it retries via `sudo` for interactive users and exits 1
  with a clear error if the running service is still the old version

## [0.3.0] - 2026-09-15

### Changed

- Frontend stack upgraded to current majors: React 19, react-router 7,
  Tailwind CSS 4 (CSS-first config, same bronze/gold theming), Vite 8
  (Rolldown; builds are ~8× faster), and ESLint 10
- Backend dependency floors raised to the latest tested versions: paramiko 5,
  cryptography 50, anthropic 1.5, openai 3.14, mcp 2.2, structlog 26,
  textual 8.2, plus fastapi, uvicorn, sqlalchemy, alembic, pydantic, and
  httpx line bumps; full suite (378 tests) validated on Python 3.11 and 3.14
- CI and the Docker build now use Node 22
- Alembic `path_separator` deprecation warning fixed

### Removed

- Unused frontend dependencies: react-hook-form, zod, @hookform/resolvers,
  class-variance-authority, and the empty root `package-lock.json` stub

## [0.2.4] - 2026-08-19

### Added

- Live visibility into running operators (closes #23): tool calls in the
  activity feed now show redacted argument previews, expandable to the full
  (still redacted) arguments in the operator activity drawer
- Loop detection: an operator run aborts after 3 consecutive identical tool
  calls and reports why, instead of silently burning tokens. Configurable via
  `VIGILUS_LOOP_DETECTION_THRESHOLD` (`0` disables)
- Running turns show live iteration progress (e.g. "Scout: calling ssh_exec
  (iteration 4/10)") and per-turn token/cost totals in the operator drawer and
  running-tasks API
- Stop button in the operator activity drawer to cancel a running operator
  directly

## [0.2.3] - 2026-08-19

### Added

- Orchestrator replies stream into the chat token by token (Anthropic, OpenAI,
  Google, and OpenAI-compatible endpoints), with token usage still recorded

### Fixed

- The orchestrator's plan ("I'll have the … Operator check …") now appears as a
  chat message while the delegated work runs, instead of only once the turn ends
- Opening a chat stream no longer misses a turn's live activity when the request
  arrives before the turn finishes starting

## [0.2.2] - 2026-08-19

### Added

- Token usage dashboard at `/usage`: tokens and estimated cost over time, broken
  down by actor (Vigilus vs each Operator), model, provider, and heaviest chat
  sessions. Replaces the smaller Settings → Usage tab
- Cost estimates for direct Anthropic/OpenAI/Google models via a static
  list-price table, overridable at `<data_dir>/model_prices.json`
- MIT `LICENSE` file, `CONTRIBUTING.md`, and this changelog
- CI workflow (lint, tests, type-check, build) running on PRs into `main` and `dev`

### Fixed

- Configured commands are no longer passed through a shell, preventing shell
  interpretation of their contents
- Discord slash commands no longer crash with a `NameError` when invoked
  (missing `handle_inbound` import)

## [0.2.1] - 2026-07-10

### Added

- `vigilus update` self-update command

### Fixed

- Stuck scheduled tasks can now be cancelled
- Stale inline JIT approval cards in chat
- SPA refresh returning 404 and empty final orchestrator replies
- npm-based MCP servers failing under the hardened systemd service
- Installer creating a pip-less venv when Python is already present
- `vigilus` CLI wrapper now installed on PATH
- Unstamped databases are adopted instead of replaying the migration chain

### Changed

- Improved MCP server install/start lifecycle

## [0.2.0] - 2026-06-20

### Added

- Update notifications in the dashboard
- Docker image publishing to GHCR on releases and `main` builds

## [0.1.0] - 2026-06-19

### Added

- Initial release: React dashboard, conversational AI orchestrator with
  Operator delegation, MCP server manager, RBAC + JIT elevation, audit trail,
  scheduled tasks, server inventory, and multi-provider LLM support

[Unreleased]: https://github.com/vigilus-labs/vigilus/compare/v0.2.3...HEAD
[0.2.3]: https://github.com/vigilus-labs/vigilus/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/vigilus-labs/vigilus/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/vigilus-labs/vigilus/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/vigilus-labs/vigilus/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/vigilus-labs/vigilus/releases/tag/v0.1.0
