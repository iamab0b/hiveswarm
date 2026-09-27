# Changelog

All notable changes to Hiveswarm. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/) once 1.0 is reached.

## [Unreleased]

## [0.1.1] — 2026-09-27

### Fixed
- `hm up` on a laptop whose worker points at a remote hub started a second, empty daemon locally; it now starts only the worker and the app there (`hm up` prints which mode it is in). The desktop app inherits this.
- `hm init` overwrote an existing `worker.toml`; it now refuses and points at `hm up` or `hm migrate`.
- The desktop app found an older `hm` ahead of its own on PATH and failed or ran the wrong one; the engine's venv now always comes first, and `hm` is re-linked on every start.
- The desktop app ran engine commands through `wsl.exe --`, which let a second shell expand the script; it now uses `wsl.exe -e`.
- The desktop app used a non-interactive shell's PATH, missing agents installed through nvm, npm-global or `.bashrc`; it now captures the interactive shell's PATH (Windows directories under `/mnt` dropped).
- Engine install failed on WSL distros whose `python3` is older than 3.11 (Ubuntu 22.04); it now uses uv (installing it if needed), which fetches a suitable Python.
- A worker exited when the hub was unreachable at start-up; it now waits and retries registration, and `hm up` keeps the app running if the worker stops.
- Workers long-polling an older daemon that answers claims at once polled every second; they now fall back to `poll_seconds`.
- Release builds: the Linux `.deb` failed for lack of a package maintainer; the macOS `.dmg` failed because `dmg-license` (a macOS-only dependency) was missing from a lockfile generated on Linux; the Windows installer and portable `.exe` had the same file name. All three are fixed, CI now builds the desktop app on Windows, macOS and Linux on every push, and a release publishes the platforms that built even if one fails.

### Added
- `hm migrate`: copies a Hivemind laptop setup (`~/.config/hivemind`, the Claude token, the hub URL and token from the old services or shell) into `~/.hiveswarm`, keeps the old mirror and project-copy folders, and disables the old `hivemind-*` user services. The desktop app runs it automatically.
- The daemon diffs and merges task branches pushed as `hivemind/<id>` by workers from before the rename.
- `/api/local` reports `product` and `version`, so the desktop app can tell its own engine from an older one on the same port and replace it.

## [0.1.0] — 2026-09-26

First public release, renamed from the private project "Hivemind" (the name was taken on PyPI).

### Added
- **Hiveswarm Desktop** (`desktop/`): an Electron app for Windows, macOS and Linux that installs the engine on first run (into WSL2 on Windows, from the wheel bundled with the app), starts `hm up`, shows the swarm in its own window with a tray icon, and stops the swarm on quit. Releases are built by `.github/workflows/release.yml` on every `v*` tag; nothing is published to PyPI.
- Long-polled claims (`POST /claim` with `wait`): an idle worker lane makes ~3 requests a minute instead of one every few seconds; local copies sync once a minute and fetch only when the hub's branch moved; the app's event stream slows down on a quiet swarm and stops for hidden tabs.
- Connection log: every request to the daemon is recorded in `~/.hiveswarm/logs/net-*.jsonl`; outages are warned about in the worker log; `hm net` summarises traffic, errors and outages and `hm report` includes it.
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

[Unreleased]: https://github.com/iamab0b/hiveswarm/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/iamab0b/hiveswarm/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/iamab0b/hiveswarm/releases/tag/v0.1.0
