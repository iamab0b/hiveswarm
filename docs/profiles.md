# Profiles, agent tables and rosters

A **profile** names the models a project's swarm runs on: which one **leads** (the goal session), which one **chats** (the advisor), and which ones **code** the tasks, each with a number of lanes. You make profiles on the Agents page or in the hub's `config.toml`, pick one when you start a goal, and from the project's first task on it stays: only the lane counts of its coders can change, per project. Below the profiles, the **agent tables** (`[agents.*]` in a worker's `worker.toml`) are what actually runs; a profile writes the tables it needs by itself.

## A profile

```toml
[profiles.deep]
note = "Opus leads and reviews, two Opus medium lanes, Codex for tests"

[profiles.deep.lead]
adapter = "claude_code"
model = "opus"
effort = "max"

[profiles.deep.chat]
model = "sonnet"

[profiles.deep.coders.claude-opus-medium]
adapter = "claude_code"
model = "opus"
effort = "medium"
count = 2

[profiles.deep.coders.codex-gpt-5-5-high]
adapter = "codex"
model = "gpt-5.5"
effort = "high"
count = 1
```

- **lead**: any CLI adapter that runs interactive sessions (Claude Code, Codex, Cursor, Gemini, Antigravity, OpenCode); a hub lane cannot lead. The goal session, `hm lead` and every session of a project on the profile run on it.
- **chat**: the advisor, always Claude Code; `model` and `effort` go to its `claude -p` runs.
- **coders**: one table per thing that runs (adapter, model, effort), keyed by it: `claude-opus-medium`, `codex-gpt-5-5-high`. `count` is the lanes. Two entries that run the same thing merge into one with the counts added. A hub lane (`prime_agent`, `local_direct`) can be a coder with `count = 1`.
- `model` is passed to the CLI as `--model`; `effort` goes to Claude Code as `--effort` (`low` … `max`) and to Codex as `model_reasoning_effort` (`minimal` … `xhigh`); the other CLIs have no effort setting and a profile cannot give them one.

The Agents page shows each profile as a card: the lead and chat rows, then a row per coder with its lanes and live state; *new profile* and *edit* open an editor with the same model pickers the agent tables use. `hm profiles` lists them; `hm profiles --project <name>` shows what a project runs on. `GET /profiles`, `POST /profiles` (create or replace) and `DELETE /profiles/<name>` are the API; a profile that a started project runs on refuses every change but its note (save a different setup under a new name), and one that any project references cannot be deleted.

## A project on a profile

```toml
[projects.web]
repo_path = "/home/me/repos/web"
profile = "deep"

[projects.web.lanes]
deep-claude-opus-medium = 3
```

The **New goal** dialog has a *Profile* select above the goal: the profiles, *Any agent* (what you had before: whoever is alive, or the roster), and *New profile…*. For an existing project the choice is saved at once; for a new project it is applied right after the project is created. Under the select the profile's card shows what will run, with ×n steppers on the coders.

**Fixed once started.** A project has started once it has a task (a goal session counts). From then on its profile — including *Any agent* — cannot change: the dialog shows the select locked and `POST /projects/<name>/settings` answers 409. What still changes, per project, is the **lane count of each coder**: the ×n steppers in the dialog and in the strip at the top of the Lead page write `projects.<name>.lanes` (by agent id). The Lead page strip also shows each role's live state, and opens into the full card.

**Agent ids.** The lead runs as `<profile>-lead` and each coder as `<profile>-<key>`: `deep-lead`, `deep-claude-opus-medium`, `deep-codex-gpt-5-5-high`. Those are the agents you see on the Agents page, in `hm agents` and in the task log, and they are the project's roster: only they take the project's tasks (`hm_advise` reports the profile and scores only its coders), and they take no other project's tasks unless that project names them in its own roster. Two projects on the same profile share its lanes; a coder's table gets the larger of the two counts.

## How a profile becomes running lanes

The machine running the app keeps its `worker.toml` in step with the profiles: on every change from the app and every thirty seconds, the app asks the daemon for the tables the profiled projects need (`GET /profiles/wanted`) and writes them (`POST /api/local/materialize`, `materialize` in `ui_server.py`):

- a missing table is written with the role's `adapter`, `model`, `effort` and `concurrency`, plus the adapter settings of the machine's own table for that adapter (`config_dir`, `dangerously_skip_permissions`, `sandbox`, `timeout` …: the table named after the adapter, else the first one on it) and `profile = "<name>"`;
- a table a profile wrote earlier is updated when the profile or a count changed, and removed once no project needs it;
- a hand-written table with a wanted name is left alone and reported.

The worker then applies the file as it does for any edit: new tables start lanes and register within about ten seconds (the Agents page says *lanes start within a minute* until they do), changed counts grow or shrink the lanes, removed tables retire. The lead's table has one lane per project on the profile, so each project's goal session has its own.

This happens on the machine running the app, which in a laptop-plus-hub setup is the laptop that also runs the worker. A worker on another machine does not get the tables by itself; copy them from the app machine's `worker.toml` if that worker should take part.

## Agent tables

An agent table is an `[agents.<name>]` table in `worker.toml`: one CLI (the `adapter`) with its own model, reasoning effort and number of lanes. The table name is the agent id everywhere else: the router routes to it, the app shows it, `hm_advise` scores it, a task can prefer it. Several tables may share one adapter; they also share that provider's sign-in and rate limits, which the app and the advice point out.

```toml
[agents.opus_medium]
adapter = "claude_code"
model = "opus"
effort = "medium"
concurrency = 2

[agents.codex]
adapter = "codex"
effort = "high"
concurrency = 1
```

The worker watches `worker.toml` and applies a change within about ten seconds, without a restart: a new or newly enabled table starts its lanes and registers with the daemon; a removed or `enabled = false` table retires its lanes (each finishes the task it is on) and unregisters; a changed `model` or `effort` applies to the next task; a changed `concurrency` resizes the lanes unless `hm agents --set` has set a count for that agent, which keeps winning until it is cleared (see [config.md](config.md#lane-counts)).

The **Agents on &lt;host&gt;** table at the bottom of the Agents page (behind *show*) edits the file on the machine that runs the app: model (a dropdown of the adapter's models, or what its CLI lists, with *custom…*), effort, lanes, an on/off switch, and *add agent*. Tables a profile wrote are shown read-only there, as *written by profile deep*. It uses `GET/POST /api/local/agents` and `DELETE /api/local/agents/<name>` on the app server (the 0.2 paths under `/api/local/profiles` still answer), which edit `worker.toml` in place and leave the rest of the file (comments included) alone.

The **+/−** on each agent's card at the top of the page is the same number as the table's lanes column: `concurrency` in that agent's `worker.toml`. For an agent a profile wrote, the card says so and the count is changed on the project instead.

## Hub lanes

`[workers.prime_agent]` and `[workers.local_direct]` in the hub's `config.toml` are lanes the hub runs itself (a coding agent in a Docker container, or a bare model behind `[inference]`). They are not tables: `GET /agents` lists them with `local: true` (and `where`, the config section behind them) when they are enabled, with `alive` meaning the container is running, and the Agents page shows them as cards marked *hub lane* and as read-only rows in the table. They run one lane each; `POST /agents/<id>/capacity` refuses them, and nothing about them can be changed from the app. See [config.md](config.md#workersprime_agent-workerslocal_direct-inference).

## How routing and advice treat agents

- `GET /agents` carries `provider`, `model`, `effort`, `configured` (lanes in the file) and `profile` per agent; the Agents page shows them, and the Swarm header counts lanes per agent.
- `hm_advise` scores each agent separately. One with no history of its own borrows the history of the other agents behind the same adapter, then the adapter's prior, so a new `deep-claude-opus-medium` is not treated as an unknown agent. Its summary counts free lanes per agent and, when several share an adapter, per provider.
- Performance (Stats, probation) stays per agent id, which is the point of running several side by side.

## Rosters: projects without a profile

`projects.<name>.agents` in `config.toml` restricts a project's work to the listed agent ids or adapters:

```toml
[projects.myapp]
repo_path = "/home/me/repos/myapp"
agents = ["opus_medium", "codex"]
```

The router only considers agents on the roster (by id, or by adapter when an adapter name is listed); `hm_advise` scores only those and reports the roster in its summary; a task's explicit `agent` still wins. If nothing on the roster is alive, routing falls back to any agent and says so in the task log. The **New goal** dialog shows the roster as toggle chips under the profile select when the project runs on *Any agent* ("any" clears it); agents a profile wrote are not offered, since their lanes belong to the projects on that profile. A project on a profile has no roster of its own: its agents come from the profile.
