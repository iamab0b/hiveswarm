from __future__ import annotations

import json
import re
import subprocess
from typing import Any

import httpx

from . import db
from .config import load

TASK_TYPES = ["refactor", "test_write", "bug_fix", "feature", "docs", "review", "investigate", "frontend"]

QUESTIONS: dict[str, Any] = {
    "task_type": {
        "type": "choice",
        "instructions": "What kind of software engineering task is this?",
        "criteria": {
            "refactor": "Restructure existing code without changing behavior",
            "test_write": "Add or extend automated tests",
            "bug_fix": "Diagnose and correct incorrect behavior",
            "feature": "Add new user-facing or API capability",
            "docs": "Write or update documentation, comments, READMEs",
            "review": "Assess existing code or a diff for quality or correctness",
            "investigate": "Explore, explain, or trace code without changing it",
            "frontend": "UI, layout, styling, or client-side interaction work",
        },
    },
    "difficulty": {
        "type": "score",
        "instructions": (
            "How difficult is this for an AI coding agent? "
            "trivial = one obvious edit, no ambiguity; "
            "easy = clear scope, few files, standard patterns; "
            "moderate = multiple files or some design judgment; "
            "hard = ambiguous, cross-cutting, or needs deep codebase understanding."
        ),
        "criteria": ["trivial", "easy", "moderate", "hard"],
    },
    "reasoning": {
        "type": "score",
        "instructions": (
            "How much multi-step reasoning does this require? "
            "none = mechanical, pattern-following; "
            "some = a few dependent decisions; "
            "substantial = extended planning or tradeoff analysis."
        ),
        "criteria": ["none", "some", "substantial"],
    },
    "needs_tools": {
        "type": "noul",
        "instructions": "This task requires running commands, reading many files, or using external tools",
        "criteria": {
            "true": "Needs shell, tests, or multi-file exploration",
            "false": "Can be done from the spec and one or two files",
        },
    },
    "is_multistep": {
        "type": "noul",
        "instructions": "This task requires several dependent steps rather than one edit",
        "criteria": {
            "true": "Sequence of changes where later steps depend on earlier results",
            "false": "A single coherent change",
        },
    },
}

_EXT = re.compile(r"\.([A-Za-z0-9]{1,8})\b")


def _repo_summary(repo_path: str, base_ref: str, limit: int = 60) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", repo_path, "ls-tree", "-r", "--name-only", base_ref],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.splitlines()
    except Exception:
        return ""
    exts: dict[str, int] = {}
    for p in out:
        m = _EXT.search(p)
        if m:
            e = m.group(1).lower()
            exts[e] = exts.get(e, 0) + 1
    top = sorted(exts.items(), key=lambda kv: -kv[1])[:8]
    head = "\n".join(out[:limit])
    return f"files={len(out)} ext_counts={dict(top)}\n{head}"


def build_state(task: Any, posture: str, budget: int) -> str:
    spec = task["spec"]
    if posture in ("redacted", "jev_redacted"):
        first = spec.strip().splitlines()[0] if spec.strip() else ""
        exts = sorted(set(m.group(1).lower() for m in _EXT.finditer(spec)))
        state = f"task: {first}\nmentioned_extensions: {exts}\n"
        state += _repo_summary(task["repo_path"], task["base_ref"], limit=20)
    else:
        state = f"task:\n{spec}\n\nrepo:\n" + _repo_summary(task["repo_path"], task["base_ref"])
    return state[:budget]


def classify_task(tid: str) -> dict[str, Any]:
    cfg = load()
    task = db.task_get(tid)
    if task is None:
        raise ValueError(f"no task {tid}")
    proj = cfg.project(task["project"])
    posture = proj.get("posture") or cfg.get("decide.posture") or cfg.get("decide.jev.posture", "redacted")
    budget = int(cfg.get("decide.state_budget_chars", 12000))

    backend_override = "local" if posture == "local" else None
    state = build_state(task, posture, budget)

    url = cfg.get("decide.url", "http://127.0.0.1:9000").rstrip("/") + "/decide"
    timeout = int(cfg.get("decide.timeout_seconds", 15)) * 5
    try:
        r = httpx.post(url, json={"state": state, "questions": QUESTIONS, "backend": backend_override}, timeout=timeout)
        r.raise_for_status()
        body = r.json()
        answers = body["answers"]
        source = body.get("source", "decide")
    except Exception:
        answers = None
        source = "fallback"

    if not answers:
        c = {
            "task_type": "investigate", "type_conf": 0.0,
            "difficulty": 2.5, "difficulty_conf": 0.0,
            "reasoning": None, "needs_tools": None, "needs_vision": None, "is_multistep": None,
            "source": "fallback", "raw": None,
        }
        db.classification_upsert(tid, c)
        return c

    tt = answers["task_type"]
    dif = answers["difficulty"]
    rsn = answers.get("reasoning") or {}
    c = {
        "task_type": tt.get("choice") if tt.get("choice") in TASK_TYPES else "investigate",
        "type_conf": float(tt.get("confidence", 0.0)),
        "difficulty": float(dif.get("score", 1.5)) + 1.0,
        "difficulty_conf": float(dif.get("confidence", 0.0)),
        "reasoning": (float(rsn.get("score", 0.0)) / 2.0) if rsn else None,
        "needs_tools": float(answers.get("needs_tools", {}).get("noul", 0.5)),
        "needs_vision": None,
        "is_multistep": float(answers.get("is_multistep", {}).get("noul", 0.5)),
        "source": "fallback" if source == "local" else source,
        "raw": json.dumps(answers),
    }
    db.classification_upsert(tid, c)
    return c


def diff_band(difficulty: float) -> int:
    return min(max(int(round(difficulty)), 1), 4)
