# Two machines: a hub and workers

The setup Hiveswarm grew up in: a **hub** (always-on Linux box, holds the repositories, runs the daemon and the decide service, optionally a local model) and a **worker** (the laptop where Claude Code, Codex, Cursor and Gemini are installed and signed in). Workers reach the hub over any network where the daemon's port and ssh are reachable — a LAN or a VPN/tailnet works; the hub needs no public address.

Everything below also applies to one worker on the hub itself plus extra workers elsewhere: a worker is just a machine with `worker.toml`.

## Hub

```bash
uv tool install hiveswarm
hm init --project myapp --repo ~/repos/myapp
```

Edit `~/.hiveswarm/config.toml`:

```toml
[daemon]
host = "0.0.0.0"          # or the hub's VPN address
hub_ssh = "you@hub"       # what workers put in front of the repo path: git clone you@hub:/home/you/repos/myapp
```

Workers need ssh access to the hub as that user (an ssh key in `~/.ssh/authorized_keys` on the hub). `hm init` chose a decide backend; to use a model running on the hub:

```toml
[decide]
backend = "local"
[decide.local]
url = "http://127.0.0.1:8080/v1/chat/completions"
model = "coder"
```

Start the services — as a first test, in two terminals:

```bash
hiveswarm-decide
hiveswarm-daemon
```

or `hm up --no-ui` runs both plus a worker on the hub (useful when the hub also has agent CLIs). Note the token: `grep TOKEN ~/.hiveswarm/env`.

## Worker

```bash
uv tool install hiveswarm
mkdir -p ~/.hiveswarm
printf 'HIVESWARM_URL=http://hub:7778\nHIVESWARM_TOKEN=<the hub token>\n' > ~/.hiveswarm/env
chmod 600 ~/.hiveswarm/env
```

Write `~/.hiveswarm/worker.toml` from [`worker.example.toml`](../worker.example.toml): set `daemon_url`, `hub_ssh`, and keep the `[agents.*]` tables for the CLIs you have. Then:

```bash
hm login claude          # once
hm status                # the daemon answers
hiveswarm-worker         # registers the agents; leave it running
hm ui                    # the app, on this machine, talking to the hub
```

`hm ui --host 0.0.0.0` makes the app reachable from other devices on the same network (a phone on the VPN, say); the daemon token is never sent to the browser.

The worker keeps a copy of every project's branch under `~/hiveswarm/<project>` and fast-forwards it as tasks merge, so the code the swarm produces is on the laptop without any manual pull; `hm sync` forces one.

## systemd

`deploy/systemd/` has user units for all four processes. On the hub:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/hiveswarm-decide.service deploy/systemd/hiveswarm.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hiveswarm-decide hiveswarm
loginctl enable-linger $USER
```

On a worker: `hiveswarm-worker.service` and `hiveswarm-ui.service` the same way. The units read `~/.hiveswarm/env` and expect the console scripts in `~/.local/bin` (where `uv tool install` and `pipx` put them). Logs: `journalctl --user -u hiveswarm -f`.

Interactive sessions from a worker service need tmux and the agents' sign-ins available to that user; `hm login claude` handles Claude Code, the others keep their own credential files.

## WSL2 workers

Works as a normal Linux worker. Two things to know:

- `hm ui` opens the Windows browser through `wslview`/`explorer.exe` when present; the "open folder" button on the Lead page opens the project's local copy in Explorer via `wslpath`.
- The app is at `http://localhost:7790` from Windows; Windows reaches WSL2's localhost automatically.

## A local model box

A machine with a GPU can serve two roles:

1. **Classifier** — run any OpenAI-compatible server (llama.cpp's `llama-server`, vLLM, Ollama with `/v1`) and point `[decide.local]` at it. Classification prompts are short; a 7–14B instruct model is plenty.
2. **Hub-local agent lane** — an agent that runs on the hub itself, inside Docker, working off the same worktrees. `deploy/hub/` has an image and an egress-only Docker network for it; enable with `[workers.prime_agent] enabled = true`. The router treats it like any other agent, with its own row in Stats.

Neither is required: a laptop-only swarm with the `openai` or `mock` backend is a complete setup.

## Troubleshooting

- `hm status` fails from the worker → the daemon's `host` is `127.0.0.1`, the port is firewalled, or the token differs. `curl http://hub:7778/health` needs no token.
- A task sits in `assigned` → no worker claims for that agent: check `hiveswarm-worker`'s log for `registered <agent>`; an agent whose binary is not on PATH is skipped with a warning. Unclaimed assignments return to the queue after `daemon.claim_seconds`.
- `git clone` fails in the worker log → `hub_ssh` is wrong or the ssh key is missing; try `git ls-remote you@hub:/path/to/repo` from the worker.
- Verification fails with "No module named pytest" → the verify image lacks the project's dependencies; set `projects.<name>.verify_image` to one that has them, or use `verify.mode = "local"` on a hub that does.
- `hm report > report.txt` collects daemon health, agents, the inbox, sign-in state, standing orders, recent tasks and the logs of recent failures (no secrets).
