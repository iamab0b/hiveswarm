"""Overlap detection: two live tasks in the same project editing the same file."""
from __future__ import annotations

import re
import threading
from typing import Any

from . import db

_lock = threading.Lock()
_files: dict[str, set[str]] = {}
_warned: set[tuple[str, str, str]] = set()
_STATUS = re.compile(r"^(?:[ MADRCU?!]{1,2}\s+)?(.+?)$")


def parse(summary: str) -> set[str]:
    out: set[str] = set()
    for part in summary.split(", "):
        part = part.strip()
        if not part or part.startswith("(+"):
            continue
        part = re.sub(r"\s*\(\+\d+ more\)$", "", part)
        m = _STATUS.match(part)
        if not m:
            continue
        path = m.group(1).strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            out.add(path)
    return out


def note_files(tid: str, project: str, summary: str) -> None:
    files = parse(summary)
    if not files:
        return
    live = {r["id"]: dict(r) for r in db.all_("SELECT id, project, state, claimed_by, session FROM tasks "
                                                "WHERE project = ? AND state IN ('claimed','running','verifying')", project)}
    with _lock:
        _files[tid] = files
        for other, of in list(_files.items()):
            if other == tid:
                continue
            if other not in live:
                _files.pop(other, None)
                continue
            common = sorted(files & of)
            for f in common:
                key = (min(tid, other), max(tid, other), f)
                if key in _warned:
                    continue
                _warned.add(key)
                agent = live[other]["claimed_by"] or "another agent"
                db.log_append(tid, "daemon", f"overlap: {f} is also being changed by {other[:8]} ({agent}) — merges may conflict")
                me = (live.get(tid) or {}).get("claimed_by") or "another agent"
                db.log_append(other, "daemon", f"overlap: {f} is also being changed by {tid[:8]} ({me}) — merges may conflict")


def forget(tid: str) -> None:
    with _lock:
        _files.pop(tid, None)


def current() -> dict[str, Any]:
    with _lock:
        return {k: sorted(v) for k, v in _files.items()}
