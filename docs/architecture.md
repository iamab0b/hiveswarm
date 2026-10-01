# Architecture

Hiveswarm is three long-running processes and a CLI, all in one Python package (`src/hiveswarm/`), plus a React app served by the CLI.

```
hm · web app · hiveswarm-mcp ──HTTP──► daemon ◄──HTTP──► decide
                                         ▲
                                   HTTP (token)
                                         │
                                      worker(s) ──ssh/git──► hub repositories
```

## Processes

### daemon (`hiveswarm.daemon`)

FastAPI over SQLite (WAL). Owns the data model and all decisions:

- **classify** (`classify.py`) — every new task is sent to the decide service with a *state* built from the spec and a summary of the repo. The `posture` decides how much of the spec the classifier sees (`redacted` sends the first line and file extensions; `full` sends everything; `local` forces the local backend). Answers: task type, difficulty (1–5, banded 1–4 for routing), whether it needs tools, vision or several steps. A failed call yields a neutral classification with `source = "fallback"`, so nothing blocks on the classifier.
- **route** (`router/roundrobin.py`) — candidates are the alive agents whose capabilities cover the classification, minus agents that already failed this task, minus agents on probation for this task type when the task is hard (difficulty band ≥ 3 on a 1–4 scale). A `preferred_agent` on the task (from the lead or `hm add --agent`) wins when it is free. Ties break round-robin among idle lanes.
- **dispatch** (`daemon/dispatcher.py`) — a loop every `daemon.poll_seconds`: expire leases of workers that stopped heartbeating, requeue assignments nobody claimed within `daemon.claim_seconds`, classify pending tasks, run the stall watch, route one classified task. Workers `POST /claim` for their agent and receive the task plus a *handoff* note summarising earlier failed attempts.
- **verify** (`verify/runner.py`) — when a worker reports a finished attempt with changes, the daemon runs the task's acceptance command against the pushed branch: in Docker (`verify.mode = "docker"`, image from the project or `verify.default_image`, network `verify.network`) or straight in a worktree (`local`); `auto` picks Docker when it is available. Pass → `done`; fail → the attempt is recorded and the task requeued with a handoff, up to `max_attempts` (3), then `failed`.
- **perf** (`perf.py`) — from each attempt's log it derives step metrics: durations from a `tool` entry to its `result`, think gaps, longest silence, time to first action. Aggregated per agent × task type into a table with a status: `healthy`, `slow` (average step ≥ 30 s and ≥ 2× the best agent on that type, or ≥ 30 % of steps over 120 s, or the last few attempts 1.8× slower than before), `failing` (< 40 % pass over ≥ 3 attempts), `unknown` (< 2 attempts). `slow` and `failing` mean probation. The same loop flags a running lane as `stalled` when one tool call has had no result for `watch.stall_minutes` or nothing at all was emitted for `watch.silence_minutes`; the flag clears itself when output resumes.
- **sessions** (`sessions.py`, routes under `/sessions`) — interactive tasks. The worker hosts them; the daemon holds the state machine (`turn`, `attention`, pending commands), the risk gate for tool calls (`sessions.risk_gate`: built-in patterns, or the decide service, or off) and the auto-approval ceiling (`sessions.auto_approve_max_risk`).
- **directives** (`directives.py`) — standing orders for a task, a session or a whole project: the prompt block, the reminder cadence (every N tool calls or M minutes), and the audit, which sends the agent's recent activity (and the screen, for agents without hooks) to the decide service and flags a likely violation on the task (`flags` → the inbox, `hm_wait`).
- **projects** (`projects.py`) — listing with the hub's `HEAD`/branch and each worker's local copy; deletion (`purge` also removes the repository when it sits under `paths.projects_root`) with tombstones so workers move their copies to `.trash/`.
- **advise** (`advise.py`, `POST /advise`) — for a list of planned specs: classification, the best agent per task from the stats table, headless vs session, an estimated pass probability and how many waves the current capacity implies. This is what the lead's `hm_advise` calls.

### decide (`hiveswarm.decide`)

A small service that answers structured questions about a piece of text — the daemon never talks to a model directly. `POST /decide` takes `state` and `questions` (a JSON schema of the answers wanted) and returns `answers` plus `source`. Answers are cached by (model, questions, state). Backends (`decide/backends.py`):

| backend | what it talks to | key |
|---|---|---|
| `openai` | any OpenAI-compatible chat-completions API (`decide.openai.url`, `.model`) | `OPENAI_API_KEY` or `HIVESWARM_DECIDE_API_KEY` |
| `local` | a model you run yourself (`decide.local.url`, `.model`), same protocol | none |
| `jev` | the hosted classifier the project was first built against | `TYPESAFE_API_KEY` |
| `mock` | fixed answers; no network | none |

`decide.backend` is tried first, `decide.fallback` (default `mock`) when it errors. A request may force a backend (`posture = "local"` does).

### worker (`hiveswarm.workers.remote`)

One per machine that has agent CLIs. It registers each configured agent with a capacity (`concurrency`), runs one polling *lane* per unit of capacity, and for each claimed task:

1. keeps a mirror of the hub repository under `root/` (cloned over `hub_ssh`, or a plain path on one machine), adds a worktree at the task's `base_ref`;
2. runs the adapter (`workers/adapters.py`, see [adapters.md](adapters.md)) with the spec, the acceptance command and the handoff; streams `msg`/`tool`/`result`/`out` entries to the daemon in batches with timestamps;
3. commits whatever changed, pushes `hiveswarm/<task id>` to the hub, reports the attempt.

The session host (`workers/session_host.py`) runs interactive sessions in a dedicated tmux server (`tmux -L hm`). Claude Code sessions get a `--settings` file whose hooks call back into the worker (permission requests, questions, tool use, stop), which is how permissions and standing-order reminders are delivered mid-turn; other agents are watched through a screen-scraping monitor that recognises their prompts and gets reminders typed in when they sit idle. The web app's terminal attaches to the tmux window over a websocket through `hm ui`, with clipboard support through OSC 52.

`workers/local_copy.py` keeps `local_projects/<project>` fast-forwarded to the hub's branch and moves deleted projects to `.trash/`. `workers/claude_auth.py` gives sessions their own Claude config directory and the token stored by `hm login claude`.

### hm, the app, the MCP server

`hm` (`cli.py`) is a thin HTTP client over the daemon, plus `init`/`up`/`login`/`ui`. `hm ui` (`ui_server.py`) serves the built React app from `ui_dist/` (its look is specified in [DESIGN.md](../DESIGN.md)), proxies `/api` to the daemon, exposes `/api/events` (SSE), the terminal websocket and, on a worker machine, the profile editor for `worker.toml` (`/api/local/profiles`, written through `tomledit.py`). `hiveswarm-mcp` (`mcp_server.py`) exposes 43 `hm_*` tools over stdio for the lead or any MCP client, 22 of them in the advisor role (`HIVESWARM_ROLE=advisor`: read-only views plus the plan, pause/resume, briefs and memory); `lead.py` and `lead_skills.py` hold the lead's prompt and skills.

### Craft, the advisor, memory

- `rulesets.py`: the Craft ruleset text, resolved per project at claim time and put into every prompt (and installed as a `hiveswarm-craft` skill for Claude Code lanes); the handoff parser; the rules for the `untested` flag. The dispatcher reads the handoff back when an attempt finishes: Deferred lines go to the `deferred` table, an untested attempt gets a task flag that merge refuses until acknowledged ([craft.md](craft.md)).
- `advisor.py`: the plan (`plans`), pauses (`pauses`), briefs (`briefs`) and the advisor's conversation (`advisor_messages`, `advisor_turns`, `advisors`). A turn is run by a worker's session host as headless Claude Code resuming the project's advisor session; the dispatcher skips paused projects ([advisor.md](advisor.md)).
- `memory.py`: the `none` / `local` (FTS5 `memories`) / `hindsight` backends; the dispatcher stores a lesson per finished attempt, prompts recall what matches ([memory.md](memory.md)).
- Profiles are plain `[agents.*]` tables; the worker's `LaneManager` re-reads `worker.toml` on change and the daemon keeps each agent's `provider`, `model` and `effort` ([profiles.md](profiles.md)).

## Data model

SQLite tables (`schema.sql`, migrated in place by `db.py`): `tasks` (with `kind`, `session` JSON, `preferred_agent`, `flags`), `classifications`, `attempts` (outcome, tokens, cost, the step metrics `steps, step_avg_s, step_p90_s, step_max_s, slow_steps, silence_max_s, first_action_s, think_avg_s`, and `ruleset`, `lines_changed`, `untested`, `handoff`), `agent_stats`, `quota_windows`, `task_logs` (the event stream every view is built from), `agents` (with `capacity`, `desired_capacity`, `provider`, `model`, `effort`), `decision_cache`, `session_commands`, `directives`, `project_local`, `project_tombstones`, `deferred`, `plans`, `pauses`, `briefs`, `advisor_messages`, `advisor_turns`, `advisors`, and `memories` (+ its FTS index) when the local memory backend is on.

Task states: `pending → classified → assigned → claimed → running → verifying → done | failed | abandoned`. Sessions add `turn` (`working`, `idle`) and `attention` (`permission`, `question`, `input`, `usage_limit`, `handoff`, `failed`, `directive`, `stalled`) inside `tasks.session`; `untested` is a task flag that outlives a done task until it is merged with an acknowledgement or cleared.

## Trust boundaries

- Every daemon route needs `Authorization: Bearer $HIVESWARM_TOKEN` when the token is set (`hm init` always sets one).
- Agents run with your user's permissions unless `sandbox_wrapper` wraps them (`scripts/hm-sandbox` is a bubblewrap example). Headless runs pass `--dangerously-skip-permissions`-style flags when the worker config says so; interactive sessions use the permission mode you pick.
- The verifier's Docker network can be an egress-only network (`deploy/hub/`) so containers reach package registries but not your LAN.
- What the classifier sees is governed by `posture`; `redacted` is the default.
