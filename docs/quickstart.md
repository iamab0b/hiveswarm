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
pip install --user ./hiveswarm-0.4.0-py3-none-any.whl
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

This starts the decide service, the daemon, the worker and the web app in the current terminal, prefixing each line with its source, and opens http://127.0.0.1:7790: one terminal, a fresh chat agent. Ctrl-C stops everything. Later, when you want them as services, see [two-machines.md](two-machines.md#systemd).

## 5. Sign Claude Code in once

Swarm sessions use their own Claude config directory (`~/.claude-worker`), so they never touch your own `claude`. Give it a long-lived token once:

```bash
hm login claude
```

It runs `claude setup-token`, stores the token at `~/.hiveswarm/claude-token` (mode 600), pre-answers Claude's first-run dialogs and runs a one-line test turn. `hm login` with no argument shows who is signed in. Codex, Cursor and Gemini use the sign-in you already have.

## 6. First project

The app opens on one terminal: the chat agent, a Claude Code session that belongs to no project yet ([chat.md](chat.md)). Type to it. To build something, paste the brief:

> Add a --version flag to the CLI and a test for it.

It proposes a project name and a profile and asks for a yes, or say `myapp` to use the project `hm init` made. It then starts the project's **lead**, whose terminal appears beside the chat agent's. The lead plans, tells you in one line what the swarm will look like, and dispatches; every agent it starts gets a tile, and an approval or a question shows up as a panel on the tile that has it. Finished work collects as chips at the bottom of the workspace; a chip opens the diff and the merge button. [terminals.md](terminals.md) describes the layout and the keys (<kbd>?</kbd> lists them).

The same pieces from a shell:

```bash
hm lead myapp "Ship user avatars: upload, resize, serve. Tests for each piece."
hm add myapp "Add a --version flag to the CLI and a test for it" -a "python -m pytest -q"
hm session new myapp "Refactor the config loader; ask me before changing the file format"
hm list
hm show <id>
```

`hm add` is one headless task: an agent works in a fresh worktree, the acceptance command must exit 0, and the result lands on a branch `hiveswarm/<task id>`. `hm session new` is an agent in its own terminal. Both appear as tiles in the project's workspace.

Every agent works under the Craft ruleset ([craft.md](craft.md)): it ends with a Changed / Tested / Deferred handoff, deferred shortcuts collect in a ledger in the project's settings, and a change with no test behind it is marked *untested* until you look. The Control tab (<kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd>) is where the projects, the sign-ins, the profiles (which model leads and which coders work, with lanes per coder: [profiles.md](profiles.md)) and the agents live.

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
