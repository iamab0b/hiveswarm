# Changelog

All notable changes to Hiveswarm. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/) once 1.0 is reached.

## [Unreleased]

## [0.2.1] — 2026-10-01

One lane count per agent, hub lanes on the Agents page, and a model picker. Upgrading: install the 0.2.1 wheel on the hub and let the desktop app upgrade the laptop; the database adds one column (`agents.configured`) by itself. A lane count set from the app in 0.2.0 was a daemon-side override; `hm agents` shows it under `wanted`, and "back to worker.toml" on the card (or `hm agents --set <agent> auto`) clears it.

### Changed
- **One lane count.** The +/− on an agent's card and the lanes column of the profiles table are now the same number: `concurrency` in that agent's `worker.toml`, which the app edits in place when the file is on its machine and the worker applies within ~10 s. In 0.2.0 the card set a daemon-side override that silently won over the file (the two controls could disagree). The override still exists for workers on other machines and for `hm agents --set`, and is now shown as such ("set with hm agents --set; worker.toml says 2") with a link back; `GET /agents` carries `configured` (the file's value, reported by the worker) beside `desired_capacity` (the override), and `hm agents` has a `config` column. The Agents page subtitle and [docs/config.md](docs/config.md#lane-counts) say which is which.
- The **model** column of the profiles table is a dropdown: Claude Code, Codex and Gemini get a published list of aliases and ids, Cursor, Antigravity and OpenCode what their CLI lists (`--list-models` / `opencode models`, read at most every ten minutes), plus "custom…" for anything else and "default" for none. `GET /api/local/profiles` carries `adapters[].models` and `models_from`.
- The effort dropdown showed an empty box for "default"; it now says so.

### Added
- **Hub lanes on the Agents page.** `prime_agent` (a coding agent in a Docker container on the hub) and `local_direct` (a bare model behind `[inference]`) appear on `GET /agents`, `hm agents`, `hm_agents` and the Agents page like a worker's agents: a card with status (*up* when the container is running), host, model and what they can do, marked *hub lane*, and a read-only row in the profiles table. They are configured in the hub's `config.toml` only (`local: true`, `where` on the row), run one lane each, and `POST /agents/<id>/capacity` refuses them. The daemon notices `[workers.*]` changes without a restart, and a missing `docker` no longer errors.

## [0.2.0] — 2026-10-01

The app got a design system, every agent got a way of working, and the swarm got an advisor, profiles and memory. Upgrading: install the 0.2.0 wheel on the hub and let the desktop app upgrade the laptop; the database migrates itself (new columns and tables), existing `worker.toml` and `config.toml` need no change. The Craft ruleset is on by default (`[rulesets] default = "off"` turns it off); memory stays off until `[memory] backend` is set.

### Changed
- The web app has a design system ([DESIGN.md](DESIGN.md)) and was reworked to it: one accent colour for *needs you* and the primary action, green and red only for finished and failed, agents as neutral chips with a hue dot, no glows or coloured borders, a hairline grid, one type scale. The Lead chat reads as a document (speaker labels, GitHub-flavoured markdown, tool calls collapsed to one line), the Stats page has pass-rate and wall-time charts, and the composer has its send button inline. `docs/screenshots/before/` keeps four pages from before the rework.

### Added
- **Hiveswarm Craft** ([docs/craft.md](docs/craft.md)): a ruleset every coding agent works under, in every headless prompt and session and as a `hiveswarm-craft` skill for Claude Code lanes: understand first, a production-code ladder (adapted from Ponytail, MIT), tests required but not excess, and a Changed / Tested / Deferred handoff. The daemon reads the handoff back: Deferred lines fill a per-project ledger (`hm deferred`, `hm_deferred`, the Lead page); an attempt that changed code without touching or running a test is flagged `untested`, stays in the inbox, and is refused by `hm_merge` and the merge button until a reviewer retries it or acknowledges the risk. Attempts record their ruleset and lines changed; `hm stats` and the Stats page compare attempts with and without it. `[rulesets]` and per-project `ruleset` / `ruleset_intensity` (`standard` / `strict` / `off`); the daemon re-reads `config.toml` when it changes.
- **Memory** ([docs/memory.md](docs/memory.md)): `[memory] backend = "local"` (an FTS5 index in the daemon's database) or `"hindsight"` (a Hindsight server, one bank per project plus global). The daemon keeps one line per failed attempt (with the verifier's last lines) and per piece of work that landed; `hm_recall` / `hm_remember` for the lead and the advisor, `hm recall` / `hm remember` from the shell, `GET /memory/recall`. What matches the lead's goal goes into its prompt; what matches your message goes into the advisor's. Off by default.
- **The advisor** ([docs/advisor.md](docs/advisor.md)): a chat beside the lead on the Lead page. Claude Code run headless on a worker with the MCP server in a read-only *advisor* role (`HIVESWARM_ROLE=advisor`) plus `hm_plan` / `hm_plan_get` (the project's plan on file, shown on the Lead page), `hm_pause` / `hm_resume` (no new task starts and nothing merges while paused) and `hm_brief` (an update for the lead, returned by `hm_wait` and typed into an idle lead's terminal; undelivered briefs go into the next lead's prompt). It answers in the i-have-adhd style (MIT). The lead saves its plan with `hm_plan` and resumes after acting on a brief.
- **Agent profiles** ([docs/profiles.md](docs/profiles.md)): any number of `[agents.*]` tables per adapter, each with its own `model`, `effort` (`--effort` for Claude Code, `model_reasoning_effort` for Codex) and lanes. The worker applies changes to `worker.toml` within ~10 s without a restart (new profiles start, removed ones retire, model/effort/lanes update); the Agents page edits the file in place. `GET /agents` reports `provider`, `model` and `effort`; `hm_advise` scores profiles separately, borrows a new profile's history from its siblings, and counts lanes per provider.
- **Project rosters**: `projects.<name>.agents` limits a project's work to the listed agents or adapters; toggle chips in the New goal dialog edit it (`POST /projects/<name>/settings`, which also sets the ruleset).
- Live lane counts: the Agents page (+/− on each card) and `hm agents --set <agent> <n|auto>` change how many parallel lanes an agent runs while the worker runs — new lanes start claiming at once, surplus lanes exit after their current task; `0` pauses an agent; the setting survives worker restarts. `POST /agents/{id}/capacity`, `desired_capacity` and `provider` on `GET /agents`.

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

[Unreleased]: https://github.com/iamab0b/hiveswarm/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/iamab0b/hiveswarm/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/iamab0b/hiveswarm/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/iamab0b/hiveswarm/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/iamab0b/hiveswarm/releases/tag/v0.1.0
