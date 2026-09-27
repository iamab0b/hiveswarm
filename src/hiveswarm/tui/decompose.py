from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

_JSON = re.compile(r"\[.*\]", re.S)

PROMPT = """You are decomposing a software goal into independent, verifiable tasks for a queue of coding agents.

Goal:
{goal}

Repository: {project} at {repo_path}
Common acceptance commands for this project: {templates}

Rules:
- Produce 2 to 8 tasks. Each must be doable independently in its own git worktree from the same base commit.
- Each task needs an "acceptance" shell command that exits 0 only when that task is correctly done. Prefer the project's real test runner. If a task genuinely cannot be checked mechanically, set acceptance to null.
- The acceptance command runs in a fresh, clean checkout of the task's branch inside a Linux container that has internet access and Python 3.12 (with pytest), Node 22 (npm, pnpm), Rust (cargo), and C/C++ (gcc, clang, cmake, ninja). Nothing from the agent's machine is installed there, so the command must install its own dependencies first: e.g. "npm ci && npm test", "pip install -r requirements.txt && python3 -m pytest -q", "cargo test", "cmake -B build -G Ninja && cmake --build build && ctest --test-dir build".
- Specs must be concrete enough that an agent with no other context can act on them: name files, functions, and expected behavior.
- Do not include tasks that depend on another task's output.
{new_repo_rule}- Output ONLY a JSON array, no prose, no code fence:
[{{"spec": "...", "acceptance": "..." | null}}, ...]
"""


async def decompose(goal: str, project: str, repo_path: str, templates: list[str],
                    config_dir: str | None = None, timeout: int = 180,
                    new_repo: bool = False) -> tuple[list[dict[str, Any]], str]:
    rule = ("- The repository is brand new and empty. Produce exactly ONE task that builds the whole goal, "
            "including its tests, because parallel tasks on an empty repository would conflict.\n") if new_repo else ""
    prompt = PROMPT.format(goal=goal.strip(), project=project, repo_path=repo_path or "(new, empty)",
                           templates=", ".join(templates) if templates else "none configured", new_repo_rule=rule)
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = config_dir or os.path.expanduser("~/.claude-worker")
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt, "--output-format", "text",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
    except FileNotFoundError:
        return [], "claude not found on PATH"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return [], f"claude timed out after {timeout}s"
    text = out.decode(errors="replace")
    if proc.returncode != 0:
        return [], f"claude exit {proc.returncode}: {err.decode(errors='replace')[-400:]}"
    m = _JSON.search(text)
    if not m:
        return [], "no JSON array in response:\n" + text[-800:]
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return [], f"bad JSON: {e}\n{text[-800:]}"
    tasks = []
    for it in items:
        if isinstance(it, dict) and it.get("spec"):
            tasks.append({"spec": str(it["spec"]).strip(), "acceptance": (it.get("acceptance") or None)})
    if not tasks:
        return [], "response parsed but contained no usable tasks"
    return tasks, ""
