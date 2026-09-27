from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import httpx

from .. import worktree
from ..config import load

AGENT = "local_direct"

_DIFF_FENCE = re.compile(r"```(?:diff|patch)?\s*\n(.*?)```", re.S)
_WORD = re.compile(r"[A-Za-z0-9_./-]{3,}")
_MAX_FILE_CHARS = 6000
_MAX_FILES = 8


def _tree(wt: str) -> list[str]:
    r = subprocess.run(["git", "ls-files"], cwd=wt, capture_output=True, text=True, timeout=30)
    return r.stdout.splitlines()


def _relevant_files(wt: str, spec: str, files: list[str]) -> list[str]:
    tokens = set(t.lower() for t in _WORD.findall(spec))
    scored: list[tuple[int, str]] = []
    for f in files:
        name = Path(f).name.lower()
        stem = Path(f).stem.lower()
        score = 0
        if f.lower() in spec.lower():
            score += 10
        if name in tokens or stem in tokens:
            score += 5
        for t in tokens:
            if len(t) >= 4 and t in stem:
                score += 1
        if score:
            scored.append((score, f))
    scored.sort(reverse=True)
    return [f for _, f in scored[:_MAX_FILES]]


def _read(wt: str, rel: str) -> str:
    p = Path(wt) / rel
    try:
        txt = p.read_text(errors="replace")
    except Exception:
        return ""
    if len(txt) > _MAX_FILE_CHARS:
        txt = txt[:_MAX_FILE_CHARS] + "\n... [truncated]\n"
    return txt


def _prompt(spec: str, files: list[str], contents: dict[str, str], acceptance: str | None) -> str:
    parts = [
        "You are a coding agent. Produce a unified diff that accomplishes the task below.",
        "Rules:",
        "- Output ONLY a unified diff inside a single ```diff fence. No prose before or after.",
        "- Paths must be relative to the repo root, in `a/path` and `b/path` form.",
        "- Use exact hunk headers like `@@ -1,2 +1,2 @@` with correct line counts, and 3 lines of context.",
        "- Copy context lines EXACTLY from the file, including indentation.",
        "- If a new file is needed, use `--- /dev/null` and `+++ b/path`.",
        "- Do not touch files not needed for the task.",
        "",
        f"## Task\n{spec}",
    ]
    if acceptance:
        parts.append(f"\n## This must pass afterward\n`{acceptance}`")
    parts.append("\n## Repository files\n" + "\n".join(files[:200]))
    if contents:
        parts.append("\n## Relevant file contents")
        for f, c in contents.items():
            parts.append(f"\n### {f}\n```\n{c}\n```")
    return "\n".join(parts)


def run(task: Any, wt: str) -> dict[str, Any]:
    cfg = load()
    url = cfg.get("inference.url")
    model = cfg.get("inference.model", "coder")
    max_tokens = int(cfg.get("inference.max_tokens", 8000))
    timeout = int(cfg.get("inference.timeout_seconds", 600))

    files = _tree(wt)
    rel = _relevant_files(wt, task["spec"], files)
    contents = {f: _read(wt, f) for f in rel}
    prompt = _prompt(task["spec"], files, contents, task["acceptance"])

    r = httpx.post(
        url,
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    body = r.json()
    text = body["choices"][0]["message"]["content"]
    usage = body.get("usage") or {}

    Path(wt, ".hiveswarm.response.md").write_text(text)

    m = _DIFF_FENCE.search(text)
    patch = m.group(1) if m else text
    if "---" not in patch or "+++" not in patch:
        return {"ok": False, "error": "no unified diff in response\n--- raw response ---\n" + text[-1500:],
                "tokens_in": usage.get("prompt_tokens"), "tokens_out": usage.get("completion_tokens"), "model": model}

    ok, err = worktree.apply_patch(wt, patch)
    if not ok:
        return {"ok": False, "error": f"patch failed: {err[:300]}\n--- diff as received ---\n{patch[-1500:]}",
                "tokens_in": usage.get("prompt_tokens"), "tokens_out": usage.get("completion_tokens"), "model": model}
    Path(wt, ".hiveswarm.response.md").unlink(missing_ok=True)

    worktree.commit_all(wt, f"hiveswarm: {task['id']}")
    return {"ok": True, "tokens_in": usage.get("prompt_tokens"), "tokens_out": usage.get("completion_tokens"),
            "model": model, "diff_stat": worktree.diff_stat(wt)}
