# Agent adapters

An adapter is the piece of Hiveswarm that knows how to drive one coding agent: how to start it on a task, what to
feed it, and how to read its output. Six ship in the box (`claude_code`, `codex`, `cursor`, `gemini`, `antigravity`,
`opencode`). You can add your own from a separate package without touching Hiveswarm.

## The contract

An adapter is a plain function:

```python
def my_agent(task: dict, wt: str, handoff: str, cfg: dict, emit=None) -> dict:
    ...
```

| argument  | meaning |
|-----------|---------|
| `task`    | the task row: `id`, `project`, `spec`, `acceptance` (shell command that must exit 0, may be empty), `kind`, `base_ref`, `repo_path` |
| `wt`      | absolute path of a fresh git worktree checked out at `base_ref`; the agent works here and must not commit |
| `handoff` | text to put in front of the spec: notes from earlier failed attempts and any standing orders; may be empty |
| `cfg`     | the `[agents.<name>]` table from `worker.toml`, verbatim (so you can define your own keys) |
| `emit`    | `emit(kind, text)` — stream progress to the app and the log; `None` when nobody is watching |

It returns a dict:

| key          | required | meaning |
|--------------|----------|---------|
| `ok`         | yes      | `True` when the agent finished normally; Hiveswarm then diffs the worktree and runs the verifier |
| `outcome`    | yes      | `"done"`, `"error"`, `"timeout"` or `"cancelled"` |
| `log`        | no       | tail of the agent's output (a few KB is plenty) |
| `tokens_in`, `tokens_out`, `cost_usd` | no | usage, shown in Stats and used by the router |

`emit` kinds the app understands: `msg` (the agent talking), `think`, `tool` (a tool call — say what it is, e.g.
`Bash: pytest -q`), `result` (the tool's result), `out` (raw output), `err`, `status`, `changes`. The per-step timers,
stall detection and the agent-performance table are computed from the gaps between `tool` and `result` entries, so
emitting those two accurately is what makes your agent measurable.

Two helpers do most of the work for a CLI agent:

```python
from hiveswarm.workers.adapters import run_cli, task_prompt

def my_agent(task, wt, handoff, cfg, emit=None):
    prompt = task_prompt(task, handoff, wt)          # spec + acceptance + house rules, same text the others get
    cmd = ["my-agent", "--non-interactive", prompt]
    if cfg.get("model"):
        cmd += ["--model", cfg["model"]]
    return run_cli(cmd, wt, env=None, timeout=int(cfg.get("timeout", 1800)), emit=emit)
```

`run_cli` runs the command in the worktree, streams stdout as `out` lines, honours cancellation from the app,
enforces the timeout and returns the dict above. Pass `parser=` (a callable `line -> [(kind, text), ...]`) to turn
your agent's JSON or log lines into `msg`/`tool`/`result` entries — see `claude_parser` and `codex_parser` in
`src/hiveswarm/workers/adapters.py` for two worked examples.

Optional attributes on the function:

```python
my_agent.capabilities = ["tools", "agent_loop"]      # what the router may assume; add "vision" if it reads images
my_agent.binary = "my-agent"                          # the executable whose presence on PATH means "installed"
my_agent.session_argv = lambda cfg, prompt, mode: ["my-agent", "--interactive", prompt]
```

`session_argv` is used for interactive sessions in the app's terminal (`hm session`, the Lead page). Without it a
session runs `<binary> <prompt>` and relies on the generic screen monitor to spot permission prompts. `mode` is
`"auto"`, `"acceptEdits"` or `"bypass"`.

## Registering it

Publish the function under the `hiveswarm.adapters` entry-point group in your package:

```toml
# pyproject.toml of your plugin
[project]
name = "hiveswarm-adapter-myagent"
dependencies = ["hiveswarm"]

[project.entry-points."hiveswarm.adapters"]
my_agent = "hiveswarm_adapter_myagent:my_agent"
```

Install it next to Hiveswarm (`pip install .` in the same environment, or `uv tool install hiveswarm --with
hiveswarm-adapter-myagent`), then enable it on a worker:

```toml
# ~/.hiveswarm/worker.toml
[agents.my_agent]
adapter = "my_agent"
concurrency = 1
timeout = 1800
model = "whatever-your-cli-accepts"
```

Restart the worker. Its log prints `adapter plugins: my_agent` and then `registered my_agent ×1 (tools, agent_loop)`.
The agent shows up in Stats and `hm agents`, the router considers it, and the lead can route to it by name.

A plugin that fails to import is logged and skipped; it never takes the worker down. A worker config that names an
adapter nobody provides is skipped with a warning listing the adapters that are available.

A complete, runnable example lives in [`examples/adapter-plugin`](../examples/adapter-plugin).

## Testing an adapter without a model

Hiveswarm's own suite drives the stack with fake `claude` and `codex` executables (`tests/fakes/`) that behave like
the real CLIs — hooks, transcripts, permission prompts — without calling a model. The cheapest way to test your
adapter is the same: write a small script named like your agent's binary that edits a file and prints something,
put it first on `PATH`, and run `hm task`. `tests/test_plugins.py` shows the mechanism end to end with a throwaway
plugin installed into a temporary directory.
