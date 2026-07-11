# Changelog

All notable changes to Vigilus are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

[Unreleased]: https://github.com/vigilus-labs/vigilus/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/vigilus-labs/vigilus/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/vigilus-labs/vigilus/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/vigilus-labs/vigilus/releases/tag/v0.1.0
