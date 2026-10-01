# Agent profiles, effort and rosters

A **profile** is an `[agents.<name>]` table in `worker.toml`: one CLI (the `adapter`) with its own model, reasoning effort and number of lanes. The table name is the agent id everywhere else: the router routes to it, the app shows it, `hm_advise` scores it, a task can prefer it. Several profiles may share one adapter; they also share that provider's sign-in and rate limits, which the app and the advice point out.

```toml
[agents.opus_medium]
adapter = "claude_code"
model = "opus"
effort = "medium"
concurrency = 2

[agents.opus_max]
adapter = "claude_code"
model = "opus"
effort = "max"
concurrency = 1

[agents.codex]
adapter = "codex"
effort = "high"
concurrency = 1
```

That is "three Opus agents, two at medium effort and one at maximum, plus Codex at high effort". `model` goes to the CLI as `--model`; `effort` goes to Claude Code as `--effort` (`low`, `medium`, `high`, `xhigh`, `max`) and to Codex as `-c model_reasoning_effort=` (`minimal` to `xhigh`). Cursor, Gemini, Antigravity and OpenCode have no effort setting; a profile for them keeps `effort` out, and the worker logs once that it is ignored.

## Changing profiles while the worker runs

The worker watches `worker.toml` and applies a change within about ten seconds, without a restart:

- a new or newly enabled table starts its lanes and registers with the daemon;
- a removed or `enabled = false` table retires its lanes (each finishes the task it is on) and unregisters;
- a changed `model` or `effort` applies to the next task the profile takes; a changed `concurrency` resizes the lanes unless the app has set a count for that agent (`hm agents --set`), which keeps winning until it is cleared.

The **Agents page** edits the same file on the machine that runs the app (the worker machine in a laptop-plus-hub setup): a table of profiles with model, effort, lanes and an on/off switch, and an "add profile" row. It uses `GET/POST /api/local/profiles` and `DELETE /api/local/profiles/<name>` on the app server, which edit `worker.toml` in place and leave the rest of the file (comments included) alone. `hm agents` lists what is registered, with the model and effort each profile runs.

## How routing and advice treat profiles

- `GET /agents` carries `provider`, `model` and `effort` per agent; the Agents page shows them, and the Swarm header counts lanes per profile.
- `hm_advise` scores each profile separately. A profile with no history of its own borrows the history of the other profiles behind the same adapter, then the adapter's prior, so a new `opus_max` is not treated as an unknown agent. Its summary counts free lanes per profile and, when several share an adapter, per provider ("lanes sharing one sign-in: claude_code 2/3 across opus_medium, opus_max").
- Performance (Stats, probation) stays per agent id: `opus_max` and `opus_medium` build separate records, which is the point of running them side by side.

## Rosters: which agents a project may use

`projects.<name>.agents` in `config.toml` restricts a project's work to the listed agent ids or adapters:

```toml
[projects.myapp]
repo_path = "/home/me/repos/myapp"
agents = ["opus_max", "codex"]
```

The router only considers agents on the roster (by id, or by adapter when an adapter name is listed); `hm_advise` scores only those and reports the roster in its summary; a task's explicit `agent` still wins. If nothing on the roster is alive, routing falls back to any agent and says so in the task log. The **New goal** dialog has the roster as toggle chips under the project ("any" clears it), saved through `POST /projects/<name>/settings`, which also takes `ruleset` and `ruleset_intensity`. The daemon re-reads `config.toml` on change, so a roster edit by hand works too.
