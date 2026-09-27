# Changelog

All notable changes to Hiveswarm. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/) once 1.0 is reached.

## [Unreleased]

## [0.1.0] — 2026-09-26

First public release, renamed from the private project "Hivemind" (the name was taken on PyPI).

### Added
- `hm init` / `hm up`: a single-machine setup in two commands; `~/.hiveswarm` as the default home; `~/.hiveswarm/env` for the URL and token.
- `openai` decide backend for any OpenAI-compatible endpoint; `decide.fallback`; `decide.posture` (`redacted` / `full` / `local`).
- `verify.mode = auto | docker | local` so acceptance commands run without Docker.
- Adapter plugin interface: `hiveswarm.adapters` entry-point group, `run_cli`/`task_prompt` helpers, optional `session_argv`; `examples/adapter-plugin`.
- Unclaimed assignments return to the queue after `daemon.claim_seconds`; workers unregister their agents on shutdown.
- `hook_port = 0` picks a free port, so several workers can share a machine.
- A pytest + Playwright suite driven by fake `claude`/`codex` binaries, and GitHub Actions CI (lint, tests on 3.11–3.13, web app build check, e2e, package smoke test).
- Documentation: quickstart, config reference, two-machine guide, lead/MCP/standing orders, architecture, adapters, release plan.

### Changed
- Package, CLI entry points, config paths and environment variables use the `hiveswarm` name; `HIVEMIND_*` variables and the old config locations still work.
- "Jev" is no longer a first-class name in the UI, CLI and MCP tool text: the classifier is "the decide model", one of four backends.
- `sessions.risk_gate` accepts `pattern` (default), `model`, `off`.
- The Claude sign-in token lives at `~/.hiveswarm/claude-token` (the old path is still read).

### Carried over from the private 0.8.x line
- Quota-aware routing with per-agent × task-type performance, probation and stall detection.
- Interactive sessions in tmux with Claude Code hooks, a screen monitor for other agents, a risk gate and an inbox.
- The lead agent with the `hiveswarm` MCP server (32 tools) and five skills.
- Standing orders with hook and typed reminders and audits.
- Project deletion with optional purge; local copies of every project on each worker.
- The web app (React, xterm.js) with Swarm, Lead, Inbox, Hive, Tasks, Stats and Agents pages, PWA install and a light theme.

[Unreleased]: https://github.com/iamab0b/hiveswarm/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/iamab0b/hiveswarm/releases/tag/v0.1.0
