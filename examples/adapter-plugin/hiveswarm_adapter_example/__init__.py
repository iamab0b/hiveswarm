"""A minimal Hiveswarm adapter plugin.

It drives a CLI that takes the whole prompt as its last argument and prints what it does — the shape most
`--print`/`exec` modes of coding agents have. Set `binary` in worker.toml to point it at the executable:

    [agents.example]
    adapter = "example"
    binary = "my-agent"
    concurrency = 1
"""
from __future__ import annotations

from typing import Any

from hiveswarm.workers.adapters import run_cli, task_prompt


def _parse(line: str) -> list[tuple[str, str]]:
    """Turn `> tool ...` lines into tool entries and `< ...` lines into results so the app can time each step."""
    s = line.rstrip("\n")
    if s.startswith("> "):
        return [("tool", s[2:])]
    if s.startswith("< "):
        return [("result", s[2:])]
    return [("msg", s)] if s.strip() else []


def example(task: dict[str, Any], wt: str, handoff: str, cfg: dict[str, Any], emit: Any = None) -> dict[str, Any]:
    binary = cfg.get("binary") or "example-agent"
    cmd = [binary, task_prompt(task, handoff, wt)]
    if cfg.get("model"):
        cmd += ["--model", str(cfg["model"])]
    return run_cli(cmd, wt, env=None, timeout=int(cfg.get("timeout", 1800)), emit=emit, parser=_parse)


example.capabilities = ["tools", "agent_loop"]
example.binary = "example-agent"
example.session_argv = lambda cfg, prompt, mode: [cfg.get("binary") or "example-agent", "--interactive", prompt]
