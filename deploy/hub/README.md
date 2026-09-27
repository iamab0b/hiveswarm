# Hub extras (optional)

Everything in here is for a hub that runs agents *itself* in Docker, next to the daemon. A hub that only holds
repositories and runs the daemon needs none of it.

- `hiveswarm-egress-guard` + `.service` — creates the `hiveswarm-egress` Docker network and iptables rules so
  containers on it reach the internet but not your LAN, VPN range (100.64.0.0/10) or the hub itself. Install with
  `sudo install -m755 hiveswarm-egress-guard /usr/local/bin/ && sudo cp hiveswarm-egress-guard.service /etc/systemd/system/ && sudo systemctl enable --now hiveswarm-egress-guard`,
  then set `verify.network = "hiveswarm-egress"` in config.toml.
- `prime-agent.Dockerfile` — an image for the optional `prime_agent` hub-local lane (a coding agent driven by a
  local model). Build it as `prime-agent:local`, run the container with your worktrees directory mounted at
  `/worktrees`, and set `[workers.prime_agent] enabled = true`. Any container that has a `prime-agent`-compatible
  CLI works the same way; see `src/hiveswarm/workers/prime_agent.py`.
