# Contributing

Thanks for looking. Hiveswarm is small enough that one person can hold it in their head; keep it that way.

## Development setup

```bash
git clone https://github.com/iamab0b/hiveswarm && cd hiveswarm
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
sudo apt install tmux            # interactive-session tests
pytest -q -m "not e2e"           # ~2 minutes: a whole stack with fake agents
```

The test suite starts a real decide service, daemon and worker in a temporary home and drives them with fake `claude` and `codex` executables (`tests/fakes/`) that behave like the CLIs — hooks, transcripts, permission prompts, AskUserQuestion — without calling any model. Nothing in the suite needs a sign-in or a network.

Browser tests: `playwright install chromium`, then `pytest -m e2e` (or set `HIVESWARM_E2E_CHROMIUM=/path/to/chrome`). Screenshots in `docs/screenshots/` are regenerated with `python tests/screenshots.py`.

Lint: `ruff check src tests examples` (CI runs it).

### The desktop app

`desktop/` is an Electron shell around `hm up` (see `desktop/README.md`). `cd desktop && npm ci && npm test` runs its unit tests; with `HIVESWARM_WHEEL_DIR=../dist` (after `python -m build --wheel`) the test also installs the engine into a scratch home and boots it, which is what CI does.

### The web app

```bash
cd webapp && npm ci && npm run dev      # live reload against `hm ui` on :7790
npm run build && rm -rf ../src/hiveswarm/ui_dist && cp -r dist ../src/hiveswarm/ui_dist
```

The built app is committed under `src/hiveswarm/ui_dist/` so installing the wheel needs no Node; CI checks that the committed build matches the sources. Rebuild it in the same PR as any `webapp/` change.

### Trying a change for real

`hm init` into a scratch home keeps your own setup untouched:

```bash
HIVESWARM_HOME=/tmp/hs hm init --project demo
HIVESWARM_HOME=/tmp/hs hm up
```

## What a good change looks like

- **Bug fixes** with a test that fails before and passes after. The `stack` fixture in `tests/conftest.py` gives you a running swarm; `Stack.post`/`get`/`wait_for` are all you usually need.
- **New agent adapters** as plugins first (see [docs/adapters.md](docs/adapters.md)); an adapter moves into the main package once it has a fake in `tests/fakes/` and a test.
- **Features** — open an issue first with the problem, not the solution. The routing, verification and session model each have a paragraph in [docs/architecture.md](docs/architecture.md); a change to one of them should update it.
- **Docs** — every config key lives in [docs/config.md](docs/config.md); every CLI change in its `--help`.

Style: Python 3.11+, type hints, `ruff` clean, 120-column lines, docstrings that say *why*. No new runtime dependencies without a reason in the PR.

## Reporting a problem

`hm report > report.txt` collects daemon health, agents, the inbox, sign-in state, standing orders, recent tasks and the logs of recent failures, without secrets. Attach it to the issue with what you expected to happen.

## Security

Hiveswarm runs coding agents with your user's permissions and pushes their work to your repositories. Reports about anything that lets a task escape its worktree, reach the daemon without the token, or leak the token belong in a private report to the maintainer (see the GitHub profile) rather than a public issue.
