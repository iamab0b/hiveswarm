# Quickstart: one machine, engine only

Everything on one laptop or desktop: the daemon, the classifier, one worker and the app, from a terminal. Ten minutes if the agents are already installed. If you would rather not touch a terminal, the [desktop app](desktop.md) does steps 2–4 for you.

## 1. Prerequisites

- Linux or WSL2 (macOS works for headless tasks; interactive sessions need tmux, which Homebrew has)
- Python 3.11 or newer, git, tmux (`sudo apt install tmux`)
- Docker is optional: with it, acceptance commands run in a clean container; without it they run directly in the task's worktree
- At least one coding-agent CLI on your PATH and signed in:

| agent | binary | install |
|---|---|---|
| Claude Code | `claude` | `npm install -g @anthropic-ai/claude-code` |
| Codex | `codex` | `npm install -g @openai/codex` |
| Cursor | `cursor-agent` | `curl https://cursor.com/install -fsS \| bash` |
| Gemini CLI | `gemini` | `npm install -g @google/gemini-cli` |

## 2. Install

Download the wheel from the [Releases page](https://github.com/iamab0b/hiveswarm/releases), then:

```bash
pip install --user ./hiveswarm-0.1.1-py3-none-any.whl
```

`pipx install ./hiveswarm-*.whl` or `uv tool install ./hiveswarm-*.whl` work the same way. The web app is bundled in the wheel; no Node needed. From a checkout: `pip install -e '.[dev]'`.

## 3. Initialise

```bash
hm init --project myapp --repo ~/code/myapp
```

Output:

```
wrote ~/.hiveswarm/config.toml
wrote ~/.hiveswarm/worker.toml
wrote ~/.hiveswarm/env (daemon URL and token; `hm` reads it automatically)
project myapp → /home/you/code/myapp
agents found: claude_code (/usr/local/bin/claude), codex (/usr/local/bin/codex)
decide backend: mock (fixed answers). For real classification set OPENAI_API_KEY (or any OpenAI-compatible endpoint) and rerun with --backend openai, or point [decide.local] at a local model.
next: hm up
```

Without `--repo`, `hm init` creates an empty repository under `~/.hiveswarm/repos/<project>` to play with. The mock classifier treats everything as a medium refactor, which is fine for a first run; see [config.md](config.md#decide) for the `openai` and `local` backends.

## 4. Run

```bash
hm up
```

This starts the decide service, the daemon, the worker and the web app in the current terminal, prefixing each line with its source, and opens http://127.0.0.1:7790. Ctrl-C stops everything. Later, when you want them as services, see [two-machines.md](two-machines.md#systemd).

## 5. Sign Claude Code in once

Swarm sessions use their own Claude config directory (`~/.claude-worker`), so they never touch your own `claude`. Give it a long-lived token once:

```bash
hm login claude
```

It runs `claude setup-token`, stores the token at `~/.hiveswarm/claude-token` (mode 600), pre-answers Claude's first-run dialogs and runs a one-line test turn. `hm login` with no argument shows who is signed in. Codex, Cursor and Gemini use the sign-in you already have.

## 6. First task

A headless task: an agent works in a fresh worktree, the acceptance command must exit 0, the result lands on a branch `hiveswarm/<task id>` you can merge from the app or with `hm`.

```bash
hm add myapp "Add a --version flag to the CLI and a test for it" -a "python -m pytest -q"
hm list
hm show <id>
```

An interactive session: a real terminal in the app, with permission requests and questions surfaced as buttons.

```bash
hm session new myapp "Refactor the config loader; ask me before changing the file format"
```

The lead: a persistent Claude Code session that runs the swarm for the project.

```bash
hm lead myapp "Ship user avatars: upload, resize, serve. Tests for each piece."
```

In the app, <kbd>g</kbd> opens the same three choices. <kbd>?</kbd> lists the keys.

## 7. Where things are

| | |
|---|---|
| `~/.hiveswarm/config.toml` | the hub config: projects, decide backend, verifier, routing |
| `~/.hiveswarm/worker.toml` | this machine's agents and their settings |
| `~/.hiveswarm/env` | `HIVESWARM_URL` and `HIVESWARM_TOKEN`, read by every `hm` command |
| `~/.hiveswarm/db/hiveswarm.db` | the SQLite database (tasks, attempts, sessions, stats) |
| `~/.hiveswarm/work/` | mirrors and worktrees the agents work in |
| `~/hiveswarm/<project>/` | a copy of each project's branch on this machine, fast-forwarded as tasks merge |

`hm report > report.txt` dumps a diagnostic bundle when something breaks.
