from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, RichLog, Static, TabPane

AGENT_STYLE = {
    "prime_agent": "green", "local_direct": "green", "claude_code": "#d97757",
    "codex": "#10a37f", "antigravity": "#4285f4", "cursor": "magenta", "opencode": "yellow",
    "gemini": "#4285f4",
}

KIND_STYLE: dict[str, tuple[str, str]] = {
    "msg": ("»", "bold"),
    "tool": ("▸", "cyan"),
    "result": (" └", "dim"),
    "err": ("!", "yellow"),
    "info": ("•", "blue"),
    "status": ("◆", "bold blue"),
    "think": (" …", "dim italic"),
    "out": ("", ""),
    "you": ("›", "bold yellow"),
    "daemon": ("◆", "magenta"),
    "handoff": ("⇢", "yellow"),
    "good": ("◆", "bold green"),
    "bad": ("◆", "bold red"),
    "note": ("◆", "cyan"),
    "ask": ("?", "bold yellow"),
    "warn": ("⚠", "bold yellow"),
    "verifier": (" │", "dim magenta"),
}

TERMINAL = ("done", "failed", "abandoned")
WORKING = ("assigned", "claimed", "running", "verifying")

ATTENTION_STYLE = {
    "question": ("?", "bold yellow"), "permission": ("!", "bold red"), "input": ("›", "bold green"),
    "usage_limit": ("⏳", "bold magenta"), "handoff": ("⇢", "yellow"), "failed": ("✗", "bold red"),
}


def daemon_kind(chunk: str) -> str:
    c = chunk or ""
    if c.startswith("handoff to"):
        return "handoff"
    if c.startswith(("verifier result: pass", "verifier result: soft", "done", "merged")):
        return "good"
    if c.startswith(("verifier result:", "failed", "requeued (", "worker error")):
        return "bad"
    if c.startswith(("needs your approval", "question for you", "usage limit")):
        return "ask"
    if c.startswith("overlap:"):
        return "warn"
    if c.startswith("you"):
        return "you"
    if c.startswith(("enqueued", "classified", "retried", "requeued:", "cancelled", "session requested",
                     "session reopened", "auto-approved", "finish requested")):
        return "note"
    return "daemon"


def status_kind(chunk: str) -> str:
    c = chunk or ""
    if c.startswith(("exited", "finished but made no changes", "worker crashed", "timed out", "session host crashed")):
        return "bad"
    return "status"


LEGACY = {"stdout": "out", "stderr": "err", "worker": "status"}


def agent_style(agent: str | None) -> str:
    return AGENT_STYLE.get(agent or "", "white")


def split_source(source: str) -> tuple[str | None, str]:
    if ":" in source:
        a, k = source.split(":", 1)
        return a, k
    if source in LEGACY:
        return None, LEGACY[source]
    return None, source


def render(ts: int, kind: str, text: str, agent: str | None = None, tid: str | None = None,
           show_agent: bool = False, show_task: bool = False, max_lines: int | None = None) -> Table:
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=8, no_wrap=True)
    if show_task:
        grid.add_column(width=8, no_wrap=True)
    if show_agent:
        grid.add_column(width=12, no_wrap=True)
    grid.add_column(width=2, no_wrap=True)
    grid.add_column(ratio=1, overflow="fold")
    prefix, style = KIND_STYLE.get(kind, ("", ""))
    body = (text or "").rstrip("\n")
    if max_lines:
        rows = body.splitlines()
        if len(rows) > max_lines:
            body = "\n".join(rows[:max_lines]) + f"\n… (+{len(rows) - max_lines} lines)"
    body_style = "" if kind in ("out",) else style
    cells: list[Text] = [Text(time.strftime("%H:%M:%S", time.localtime(ts)), style="dim")]
    if show_task:
        cells.append(Text((tid or "")[:8], style="dim"))
    if show_agent:
        cells.append(Text((agent or "hive")[:12], style=agent_style(agent) if agent else "dim"))
    cells.append(Text(prefix, style=style))
    cells.append(Text(body, style=body_style))
    grid.add_row(*cells)
    return grid


def separator(agent: str, tid: str, spec: str, attempt: str = "") -> Rule:
    first = (spec or "").strip().splitlines()[0][:70] if (spec or "").strip() else ""
    title = f" task {tid[:8]}{' · attempt ' + attempt if attempt else ''}{' · ' + first if first else ''} "
    return Rule(Text(title, style=f"bold {agent_style(agent)}"), style=agent_style(agent), align="left")


@dataclass
class LiveState:
    tid: str
    agent: str
    kind: str = "task"
    started: float = field(default_factory=time.time)
    lines: int = 0
    ended_at: float | None = None
    attention: dict[str, Any] | None = None
    turn: str | None = None
    state: str = ""
    unread: int = 0
    last_screen: str = ""
    lead: bool = False

    @property
    def pane_id(self) -> str:
        return f"live-{self.tid}"


class LivePane(TabPane):
    def __init__(self, st: LiveState):
        super().__init__(f"{st.agent}·{st.tid[:8]}", id=st.pane_id)
        self.st = st

    def compose(self) -> ComposeResult:
        tid = self.st.tid
        yield Static("", id=f"lhead-{tid}", classes="panehead")
        if self.st.kind == "session":
            yield Static("", id=f"latt-{tid}", classes="attention")
            with Horizontal(classes="sessionbody"):
                yield RichLog(id=f"llog-{tid}", wrap=True, highlight=False, markup=False, max_lines=6000,
                              classes="livelog sessionlog")
                with Vertical(classes="termcol"):
                    yield Static("", id=f"lterm-{tid}", classes="terminal")
            yield Input(placeholder="message for this agent — enter sends · esc back to keys", id=f"lin-{tid}",
                        classes="sessioninput")
        else:
            yield RichLog(id=f"llog-{tid}", wrap=True, highlight=False, markup=False, max_lines=6000, classes="livelog")


def tab_label(st: LiveState) -> Text:
    t = Text()
    if st.attention and st.attention.get("kind") in ("question", "permission", "usage_limit", "failed"):
        mark, style = ATTENTION_STYLE[st.attention["kind"]]
        style = "bold reverse " + style.replace("bold ", "")
    elif st.attention and st.attention.get("kind") == "input":
        mark, style = "›", "bold green"
    elif st.state == "done":
        mark, style = "✓", "green"
    elif st.state in ("failed", "abandoned"):
        mark, style = "✗", "red"
    elif st.kind == "session":
        mark, style = ("★", "bold yellow") if st.lead else ("⌨", "bold green")
    else:
        mark, style = "●", "bold green"
    t.append(mark + " ", style=style)
    t.append(st.agent, style=agent_style(st.agent))
    t.append("·" + st.tid[:6], style="dim")
    if st.unread and st.kind == "session" and not st.attention:
        t.append(f" +{st.unread}", style="dim yellow")
    return t


def header_text(st: LiveState, task: dict[str, Any] | None, sess: dict[str, Any] | None) -> Text:
    t = Text(" ")
    t.append(st.agent, style=f"bold {agent_style(st.agent)}")
    t.append(f"  {st.tid[:8]}  ", style="bold")
    if st.kind == "session":
        t.append("lead session  " if st.lead else "interactive session  ", style="bold yellow" if st.lead else "bold green")
        if sess:
            t.append(f"{sess.get('name') or ''}  ", style="dim")
            t.append(f"{sess.get('permission_mode')}  ", style="cyan")
            turn = sess.get("turn") or ""
            turn_style = {"working": "bold cyan", "waiting": "bold yellow", "idle": "green", "exited": "dim",
                          "starting": "dim"}.get(turn, "")
            t.append(f"{turn}  ", style=turn_style)
            t.append(f"turns {sess.get('turns', 0)} · asks {sess.get('asks', 0)} · auto-ok {sess.get('auto_approved', 0)}  ",
                     style="dim")
    else:
        state = task.get("state", "") if task else st.state
        t.append(f"{state}  ", style="cyan")
        if task:
            t.append(f"attempt {task.get('attempts', '?')}/{task.get('max_attempts', '?')}  ", style="dim")
    el = int(time.time() - st.started)
    t.append(f"{el // 60}m{el % 60:02d}s  ", style="dim")
    if task:
        first = (task.get("spec") or "").strip().splitlines()[0][:70] if (task.get("spec") or "").strip() else ""
        t.append(first)
    t.append("\n ")
    if st.kind == "session":
        if st.state in TERMINAL:
            t.append("r reopen · x delete · m merge (if done) · j/k scroll", style="dim")
        else:
            t.append("enter attach terminal · t type a message · y approve · d deny · 1-9 pick option · "
                     "F finish (commit+verify) · o continue elsewhere · c cancel · j/k scroll", style="dim")
    else:
        t.append("r retry · c cancel · m merge · j/k scroll · f follow", style="dim")
    return t


def attention_text(att: dict[str, Any] | None) -> Text:
    if not att:
        return Text("")
    kind = att.get("kind", "")
    mark, style = ATTENTION_STYLE.get(kind, ("•", "bold"))
    t = Text(" ")
    t.append(f"{mark} ", style=style)
    if kind == "permission":
        t.append("needs approval", style=style)
        t.append(f"  ({att.get('risk', '?')} risk)  ", style="dim")
        t.append(att.get("summary", ""), style="bold")
        t.append("\n   y approve · d deny · t deny with a message", style="dim")
        detail = (att.get("detail") or "").strip()
        if detail and detail != att.get("summary", "").split(": ", 1)[-1]:
            t.append("\n   " + detail[:400].replace("\n", "\n   "), style="dim")
    elif kind == "question":
        t.append("asks you", style=style)
        t.append("  " + (att.get("summary") or ""), style="bold")
        opts = att.get("options") or []
        for i, o in enumerate(opts, 1):
            t.append(f"\n   {i}. ", style="bold yellow")
            t.append(str(o))
        t.append("\n   " + ("press the number, or " if opts else "") + "t to type your own answer", style="dim")
    elif kind == "input":
        t.append("waiting for you", style=style)
        t.append("  " + (att.get("summary") or ""), style="")
        t.append("\n   t to send the next instruction · F to finish (commit, push, verify) · enter to attach", style="dim")
    elif kind == "usage_limit":
        t.append("usage limit", style=style)
        t.append("  " + (att.get("summary") or ""), style="")
        t.append("\n   o continue on another agent now (work is carried over) · or wait and r reopens it later", style="dim")
    elif kind == "failed":
        t.append("failed", style=style)
        t.append("  " + (att.get("summary") or ""), style="")
    else:
        t.append(kind, style=style)
        t.append("  " + (att.get("summary") or ""), style="")
    return t
