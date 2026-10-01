# Configuration reference

Two TOML files, both written by `hm init` and both optional key by key — anything missing takes the default shown here.

- **`config.toml`** — the hub: read by the daemon, the decide service and `hm`. Found at `$HIVESWARM_CONFIG`, then `~/.hiveswarm/config.toml`, then `~/.config/hiveswarm/config.toml`.
- **`worker.toml`** — one per machine that runs agents: read by `hiveswarm-worker`. Found at `$HIVESWARM_WORKER_CONFIG`, `--config`, then `~/.hiveswarm/worker.toml`, then `~/.config/hiveswarm/worker.toml`.

`$HIVESWARM_HOME` moves the whole `~/.hiveswarm` directory. `~/.hiveswarm/env` (`KEY=value` lines) is loaded into the environment by every Hiveswarm process, which is where `hm init` puts the token. Paths may start with `~`.

## config.toml

### `[paths]`

| key | default | |
|---|---|---|
| `projects_root` | `~/.hiveswarm/repos` | where project repositories live on the hub; `hm project delete --purge` only removes repositories under it |
| `db` | `~/.hiveswarm/db/hiveswarm.db` | SQLite database |
| `worktrees` | `~/.hiveswarm/worktrees` | the daemon's verify worktrees |
| `logs` | `~/.hiveswarm/logs` | log files when run as services |

### `[daemon]`

| key | default | |
|---|---|---|
| `host` | `127.0.0.1` | bind address; `0.0.0.0` (or a VPN address) when workers are on other machines |
| `port` | `7778` | |
| `poll_seconds` | `5` | dispatcher loop interval |
| `lease_seconds` | `1800` | a running attempt whose worker stops heartbeating for this long is requeued |
| `claim_seconds` | `60` | an assignment the worker never claimed goes back to the queue after this |
| `hub_ssh` | `""` | `user@host` remote workers use to clone the hub's repositories (`git clone user@host:/path`); empty on one machine |
| `access_log` | `false` | log every HTTP request |
| `log_level` | `"info"` | |

The token is not in the file: set `HIVESWARM_TOKEN` (in `~/.hiveswarm/env`). Without it the daemon accepts every request.

### `[decide]`

| key | default | |
|---|---|---|
| `backend` | `"mock"` | `openai`, `local`, `jev` or `mock` |
| `fallback` | `"mock"` | tried when the primary backend errors; `""` for none |
| `host` / `port` / `url` | `127.0.0.1` / `9000` / `http://127.0.0.1:9000` | where the decide service listens and where the daemon finds it |
| `timeout_seconds` | `15` | per model call |
| `state_budget_chars` | `12000` | how much context the classifier gets |
| `posture` | `"redacted"` | what the classifier sees: `redacted` (first line of the spec, file extensions, repo summary), `full` (the whole spec), or `local` (force the local backend). Per project: `projects.<name>.posture` |
| `model` | backend's own | overrides the model name used in cache keys and requests |

| section | keys | |
|---|---|---|
| `[decide.openai]` | `url` (`https://api.openai.com/v1`, a base URL or a full `/chat/completions` URL), `model` (`gpt-4o-mini`) | key from `OPENAI_API_KEY` or `HIVESWARM_DECIDE_API_KEY` |
| `[decide.local]` | `url` (`http://127.0.0.1:8080/v1/chat/completions`), `model` (`coder`) | llama.cpp, vLLM, Ollama, LM Studio… anything speaking the OpenAI chat protocol |
| `[decide.jev]` | `url`, `model` | needs `TYPESAFE_API_KEY` |

### `[verify]`

| key | default | |
|---|---|---|
| `mode` | `"auto"` | `docker` (a container per verification), `local` (the acceptance command runs in a worktree on the hub), or `auto` (Docker when `docker` works, else local) |
| `default_image` | `"python:3.12-slim"` | image when the project sets none |
| `network` | `"bridge"` | Docker network for verification containers (`hiveswarm-egress` with the egress guard from `deploy/hub/`) |
| `timeout_seconds` | `600` | |
| `memory` | `"4g"` | container memory limit |

### `[sessions]`

| key | default | |
|---|---|---|
| `risk_gate` | `"pattern"` | how a session's tool call is rated before auto-approval: `pattern` (built-in rules: force pushes, `rm -rf`, secrets… are high), `model` (ask the decide service; high patterns still short-circuit), `off` (everything is medium) |
| `auto_approve_max_risk` | `"low"` | in `auto` permission mode, calls at or below this risk are approved without asking |

### `[watch]`

| key | default | |
|---|---|---|
| `stall_minutes` | `3` | one tool call with no result for this long flags the lane as stalled |
| `silence_minutes` | `6` | no output at all for this long does the same |

### `[routing]`

| key | default | |
|---|---|---|
| `probation` | `true` | keep `slow`/`failing` agents off hard tasks of that type (see [architecture.md](architecture.md#processes)) |

### `[projects.<name>]`

| key | | |
|---|---|---|
| `repo_path` | required | repository on the hub (bare or with a checkout); `hm init` and `hm project`/the app write these entries |
| `verify_image` | `verify.default_image` | Docker image for this project's acceptance commands |
| `verify_mode`, `verify_network` | from `[verify]` | per-project overrides |
| `posture` | `decide.posture` | |
| `acceptance_templates` | `[]` | suggestions shown in the New goal dialog |
| `ruleset`, `ruleset_intensity` | from `[rulesets]` | `"craft"` or `"off"`; `"standard"` or `"strict"` (see [craft.md](craft.md)) |
| `agents` | `[]` | the roster: only these agent ids (or adapters) take this project's work; the New goal dialog edits it ([profiles.md](profiles.md)) |

### `[rulesets]`

| key | default | |
|---|---|---|
| `default` | `"craft"` | the ruleset every project's agents work under unless the project says otherwise; `"off"` for none |
| `intensity` | `"standard"` | `"strict"` adds: no new dependencies, no new files unless named, stop rather than widen the scope |

Changes to `config.toml` are picked up without a restart. [craft.md](craft.md) has the rules, the handoff format, the deferred ledger and the `untested` flag.

### `[memory]`

| key | default | |
|---|---|---|
| `backend` | `"none"` | `"local"` (full-text index in the daemon's database) or `"hindsight"` (a Hindsight server); see [memory.md](memory.md) |
| `hindsight.url` | `http://127.0.0.1:8888` | |
| `hindsight.token` | none | bearer token if the server requires one |
| `hindsight.budget` | `"low"` | recall effort: `low`, `mid`, `high` |
| `hindsight.bank_prefix` | `"hiveswarm"` | banks are `<prefix>-<project>` and `<prefix>-global` |

### `[workers.prime_agent]`, `[workers.local_direct]`, `[inference]`

Optional hub-local lanes for a machine that runs a model itself. `prime_agent` drives a coding agent inside a Docker container (`container`, `worktree_mount`, `timeout_seconds`, `extra_args`); `local_direct` asks a bare model at `inference.url` (`model`, `max_tokens`, `timeout_seconds`) for a diff. Both default to `enabled = false`; the router only considers them when enabled and reachable.

## worker.toml

| key | default | |
|---|---|---|
| `daemon_url` | `http://127.0.0.1:7778` | |
| `token` | `$HIVESWARM_TOKEN` | |
| `hub_ssh` | `""` | `user@host` for cloning the hub's repositories; empty when the worker is on the hub |
| `root` | `~/.hiveswarm` | mirrors and worktrees (`hm init` uses `~/.hiveswarm/work`) |
| `local_projects` | `~/hiveswarm` | a fast-forwarded copy of every project's branch on this machine; `""` turns it off |
| `sandbox_wrapper` | none | a command that wraps every agent process (see `scripts/hm-sandbox`) |
| `poll_seconds` | `10` | wait between claims after an error, or when `claim_wait_seconds` is 0 |
| `claim_wait_seconds` | `20` | the daemon holds an idle claim this long before answering; 0 = plain polling every `poll_seconds` |
| `local_sync_seconds` | `60` | how often local copies check the hub for moved branches |
| `keep_worktrees` | `false` | keep finished worktrees for inspection |
| `hook_port` | `7791` | Claude Code hooks call back here; `0` picks a free port |

### `[agents.<name>]`

The table name is the agent id shown everywhere; `adapter` picks the implementation (defaults to the name). Several tables may share one adapter: each is a **profile** with its own model, effort and lanes (`[agents.opus_max]` with `adapter = "claude_code"`, `model = "opus"`, `effort = "max"`). The worker re-reads this file when it changes and starts, stops or re-labels lanes to match, so profiles can be added, changed and removed while it runs; the Agents page edits them. See [profiles.md](profiles.md).

| key | default | applies to | |
|---|---|---|---|
| `adapter` | the table name | all | `claude_code`, `codex`, `cursor`, `gemini`, `antigravity`, `opencode`, or a plugin ([adapters.md](adapters.md)) |
| `enabled` | `true` | all | |
| `binary` | adapter's default | all | executable to look for on PATH |
| `concurrency` | `1` | all | parallel lanes for this agent at start; the Agents page and `hm agents --set` change the live count (see below) |
| `timeout` | `1800` | all | seconds per headless attempt |
| `model` | agent's default | all | passed through to the CLI (`--model`) |
| `effort` | agent's default | claude_code, codex | reasoning effort: `low`/`medium`/`high`/`xhigh`/`max` for Claude Code (`--effort`), `minimal`…`xhigh` for Codex (`model_reasoning_effort`); other CLIs have no such setting and the worker says so once |
| `config_dir` | `~/.claude-worker` | claude_code | a separate Claude config so swarm sessions never touch your own |
| `oauth_token_file` | `~/.hiveswarm/claude-token` | claude_code | written by `hm login claude` |
| `dangerously_skip_permissions` | `false` | claude_code, antigravity | headless runs skip permission prompts |
| `permission_mode` | `acceptEdits` | claude_code | when not skipping permissions |
| `sandbox` | `workspace-write` | codex | `codex exec --sandbox …` |
| `force` | `false` | cursor | `cursor-agent --force` |
| `yolo` | `true` | gemini | `gemini --yolo` |
| `pty` | `false` | antigravity | run under a pseudo-terminal |

### Live lane counts

`concurrency` is the number of lanes a worker starts with. The number it runs is changed while it runs, without a restart, from the Agents page (the +/− control on each agent's card) or the CLI:

```bash
hm agents --set claude_code 5
hm agents --set codex 0
hm agents --set claude_code auto
```

The daemon stores the wanted count per agent and every worker running that agent checks for it every 10 seconds: new lanes start claiming at once, surplus lanes stop claiming and exit after the task they are on finishes. `0` pauses an agent (nothing is routed to it until a lane is back); `auto` (or `config`/`reset`) drops the override, and the worker goes back to `concurrency`. The limit is 32 lanes per agent. The Agents page shows "applying…" until the worker has caught up and warns when more than six lanes share one provider sign-in, since the provider's rate limits apply to all of them together. A worker that restarts asks the daemon for the wanted count before starting its lanes, so the setting survives restarts and upgrades.

## Environment variables

| variable | |
|---|---|
| `HIVESWARM_HOME` | the state directory (default `~/.hiveswarm`) |
| `HIVESWARM_CONFIG`, `HIVESWARM_WORKER_CONFIG` | explicit config paths |
| `HIVESWARM_URL`, `HIVESWARM_TOKEN` | how `hm`, the app and the MCP server reach the daemon |
| `HIVESWARM_DECIDE_API_KEY` / `OPENAI_API_KEY`, `TYPESAFE_API_KEY` | decide backends |
| `HIVESWARM_ORIGIN` | label the MCP server puts on tasks it creates |
| `HIVESWARM_NETLOG` | `0` turns off the per-request connection log (`~/.hiveswarm/logs/net-*.jsonl`) |
| `HIVEMIND_*` | the pre-rename names still work |
