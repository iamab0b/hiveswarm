from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static, TabbedContent, TabPane

from . import decompose as _decompose
from .client import Client, DaemonError
from .live import (
    AGENT_STYLE,
    ATTENTION_STYLE,
    TERMINAL,
    WORKING,
    LivePane,
    LiveState,
    attention_text,
    daemon_kind,
    header_text,
    render,
    separator,
    split_source,
    status_kind,
    tab_label,
)
from .screens import AddTaskScreen, ConfirmScreen, ContinueScreen, DecomposeScreen, HelpScreen, RetryScreen

STATE_STYLE = {
    "pending": "dim", "classifying": "dim", "classified": "yellow", "assigned": "yellow",
    "claimed": "cyan", "running": "bold cyan", "verifying": "bold magenta",
    "done": "bold green", "failed": "bold red", "blocked": "red", "abandoned": "dim red",
}

LIVE_KEEP_SECONDS = 180
LIVE_MAX_TABS = 14
TMUX_SOCKET = "hm"
TMUX_SESSION = "hm"


def _age(ts: int) -> str:
    d = int(time.time()) - int(ts)
    if d < 60:
        return f"{d}s"
    if d < 3600:
        return f"{d // 60}m"
    if d < 86400:
        return f"{d // 3600}h"
    return f"{d // 86400}d"


def _first_line(s: str | None, n: int = 70) -> str:
    if not s:
        return ""
    line = s.strip().splitlines()[0] if s.strip() else ""
    return line[:n] + ("…" if len(line) > n else "")


def _session_of(t: dict[str, Any] | None) -> dict[str, Any]:
    if not t or t.get("kind") != "session":
        return {}
    s = t.get("session")
    if isinstance(s, dict):
        return s
    try:
        return json.loads(s or "{}")
    except Exception:
        return {}


class SummaryBar(Static):
    def render_summary(self, s: dict[str, Any] | None, err: str | None, inbox: int = 0) -> None:
        if err:
            self.update(Text(f"  ✗ {err}", style="bold red"))
            return
        if not s:
            self.update("")
            return
        t = s.get("tasks", {})
        a = s.get("attempts_24h", {})
        parts = Text("  ")
        parts.append("Hiveswarm ", style="bold")
        parts.append("● " if s.get("dispatcher_alive") else "○ ", style="green" if s.get("dispatcher_alive") else "red")
        if inbox:
            parts.append(f" inbox {inbox} ", style="bold black on yellow")
            parts.append("  ")
        parts.append("  queue ", style="dim")
        for k in ("pending", "classified", "assigned", "running", "verifying"):
            n = t.get(k, 0)
            if n:
                parts.append(f"{k}:{n} ", style=STATE_STYLE.get(k, ""))
        parts.append("  done ", style="dim"); parts.append(str(t.get("done", 0)), style="green")
        parts.append("  failed ", style="dim"); parts.append(str(t.get("failed", 0)), style="red")
        parts.append("  24h ", style="dim")
        parts.append(f"✓{a.get('pass', 0)} ", style="green")
        parts.append(f"✗{a.get('fail', 0) + a.get('error', 0) + a.get('timeout', 0)} ", style="red")
        agents = s.get("agents_alive", [])
        parts.append("  agents ", style="dim")
        if agents:
            for ag in agents:
                parts.append(f"{ag} ", style=AGENT_STYLE.get(ag, ""))
        else:
            parts.append("none alive", style="dim red")
        self.update(parts)


class HiveswarmApp(App[None]):
    TITLE = "Hiveswarm"
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "help", "Help"),
        Binding("g", "decompose", "New goal"),
        Binding("a", "add", "Add task"),
        Binding("n", "next_attention", "Next ask"),
        Binding("y", "approve", "Approve"),
        Binding("d", "deny", "Deny"),
        Binding("t", "type_message", "Type"),
        Binding("F", "finish_session", "Finish"),
        Binding("o", "continue_session", "Continue on…", show=False),
        Binding("r", "retry", "Retry"),
        Binding("c", "cancel_task", "Cancel"),
        Binding("m", "merge", "Merge"),
        Binding("x", "delete", "Delete"),
        Binding("i", "inbox", "Inbox"),
        Binding("h", "hive", "Hive"),
        Binding("left_square_bracket", "top_prev", "◀", key_display="["),
        Binding("right_square_bracket", "top_next", "▶", key_display="]"),
        Binding("f", "toggle_follow", "Follow", show=False),
        Binding("s", "stats", "Stats", show=False),
        Binding("A", "agents", "Agents", show=False),
        Binding("slash", "filter", "Filter", show=False),
        Binding("R", "refresh", "Refresh", show=False),
        Binding("w", "watch", "Watch", show=False),
        Binding("0", "top('queue')", show=False),
        Binding("W", "toggle_auto_watch", show=False),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("1", "digit(1)", show=False),
        Binding("2", "digit(2)", show=False),
        Binding("3", "digit(3)", show=False),
        Binding("4", "digit(4)", show=False),
        Binding("5", "digit(5)", show=False),
        Binding("6", "digit(6)", show=False),
        Binding("7", "digit(7)", show=False),
        Binding("8", "digit(8)", show=False),
        Binding("9", "digit(9)", show=False),
        Binding("enter", "primary", show=False),
        Binding("escape", "escape", show=False),
    ]

    selected: reactive[str | None] = reactive(None)
    follow: reactive[bool] = reactive(True)
    filter_text: reactive[str] = reactive("")

    def __init__(self, url: str | None = None, token: str | None = None, poll: float = 2.0):
        super().__init__()
        self.client = Client(url, token)
        self.poll = poll
        self._tasks: list[dict[str, Any]] = []
        self._projects: dict[str, Any] = {}
        self._templates: dict[str, list[str]] = {}
        self._log_seq: dict[str, int] = {}
        self._log_task: str | None = None
        self._detail_cache: dict[str, Any] = {}
        self._err: str | None = None
        self._feed_last: int | None = None
        self._feed_ok = True
        self._task_agent: dict[str, str] = {}
        self._routes: dict[str, int] = {}
        self._feed_lock = asyncio.Lock()
        self._ticking = False
        self._live: dict[str, LiveState] = {}
        self._last_msg: dict[tuple[str, str], str] = {}
        self.auto_watch = False
        self._action_tid: str | None = None
        self._inbox: list[dict[str, Any]] = []
        self._seen_attention: set[tuple[str, str, int]] = set()
        self._inbox_selected: str | None = None
        self._hostname = socket.gethostname()
        self._tmux_ok = bool(shutil.which("tmux"))
        self._session_agents: list[str] = ["claude_code"]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield SummaryBar(id="summary")
        with TabbedContent(id="top", initial="queue"):
            with TabPane("Queue", id="queue"):
                with Horizontal(id="body"):
                    with Vertical(id="left"):
                        yield Input(placeholder="filter…  (state:running  agent:prime_agent  project:demo  or free text)", id="filter")
                        yield DataTable(id="tasks", cursor_type="row", zebra_stripes=True)
                    with Vertical(id="right"):
                        with TabbedContent(id="detail", initial="overview"):
                            with TabPane("Overview", id="overview"):
                                yield Static("", id="ov")
                            with TabPane("Log", id="log"):
                                yield RichLog(id="loglog", wrap=True, highlight=False, markup=False, max_lines=4000)
                            with TabPane("Diff", id="diff"):
                                yield RichLog(id="difflog", wrap=False, highlight=True, markup=False, max_lines=6000)
            with TabPane("Inbox", id="inbox"):
                yield Static(Text(" Inbox — everything that needs you: questions, approvals, sessions waiting for input, "
                                  "usage limits, failures.  enter opens it · y approve · d deny · 1-9 answer · n jumps to the next one",
                                  style="dim"), classes="panehead")
                yield DataTable(id="inboxtable", cursor_type="row", zebra_stripes=True)
            with TabPane("Hive", id="hive"):
                yield Static(Text(" Hive — how the agents coordinate: every enqueue, routing decision, handoff between "
                                  "attempts, verification, approvals, and what each agent reports back.  A tab appears to the "
                                  "right for every task or session in flight.", style="dim"), classes="panehead")
                yield RichLog(id="hivelog", wrap=True, highlight=False, markup=False, max_lines=8000, classes="livelog")
        yield Footer()

    async def on_mount(self) -> None:
        t = self.query_one("#tasks", DataTable)
        t.add_columns("id", "state", "att", "agent", "project", "age", "spec")
        ib = self.query_one("#inboxtable", DataTable)
        ib.add_columns("", "kind", "id", "agent", "project", "age", "what")
        self.query_one("#filter", Input).display = False
        await self._load_projects()
        self.set_interval(self.poll, self._tick)
        await self._tick()
        t.focus()

    async def _load_projects(self) -> None:
        try:
            self._projects = await self.client.projects()
        except DaemonError:
            self._projects = {}
        self._templates = {}
        for name, p in self._projects.items():
            tl = p.get("acceptance_templates") or []
            if not tl:
                img = (p.get("verify_image") or "")
                if "python" in img:
                    tl = ["python3 -m pytest -q", "python3 -m pytest -q -x"]
                elif "node" in img:
                    tl = ["npm test", "npm run build && npm test"]
                elif "rust" in img:
                    tl = ["cargo test", "cargo build && cargo test"]
            self._templates[name] = list(tl)
        try:
            agents = await self.client.agents()
            names = [a["agent_id"] for a in agents if "sessions" in (a.get("capabilities") or []) and a.get("alive", True)]
            if names:
                self._session_agents = sorted(set(names), key=lambda n: (n != "claude_code", n))
        except DaemonError:
            pass

    async def _tick(self) -> None:
        if self._ticking:
            return
        self._ticking = True
        try:
            await self._tick_inner()
        except DaemonError as e:
            self._err = str(e)
            self.query_one("#summary", SummaryBar).render_summary(None, self._err)
        except Exception as e:
            self._err = f"{type(e).__name__}: {e}"[:200]
            self.query_one("#summary", SummaryBar).render_summary(None, self._err)
        finally:
            self._ticking = False

    async def _tick_inner(self) -> None:
        try:
            summary, tasks, inbox = await asyncio.gather(self.client.summary(), self.client.tasks(), self.client.inbox())
            self._err = None
        except DaemonError as e:
            self._err = str(e)
            self.query_one("#summary", SummaryBar).render_summary(None, self._err)
            return
        self._inbox = inbox.get("items", []) if isinstance(inbox, dict) else []
        self.query_one("#summary", SummaryBar).render_summary(summary, None, len(self._inbox))
        self._notify_transitions(tasks)
        self._tasks = tasks
        self._render_table()
        await self._sync_live()
        await self._pull_feed()
        self._render_inbox()
        self._notify_attention()
        await self._refresh_terminal()
        if self.selected:
            await self._refresh_detail(self.selected)

    def _task_by_id(self, tid: str | None) -> dict[str, Any] | None:
        if not tid:
            return None
        for t in self._tasks:
            if t["id"] == tid:
                return t
        return None

    # ── live feed ──────────────────────────────────────────────────────

    async def _pull_feed(self) -> None:
        if not self._feed_ok:
            return
        async with self._feed_lock:
            try:
                if self._feed_last is None:
                    d = await self.client.feed(tail=3000)
                    live = False
                else:
                    d = await self.client.feed(since=self._feed_last)
                    live = True
            except DaemonError as e:
                if str(e).startswith("404"):
                    self._feed_ok = False
                    self.query_one("#hivelog", RichLog).write(Text(
                        "The hub's daemon is too old for the live feed. Update it, "
                        "then restart hm tui.", style="bold yellow"))
                return
            last = self._feed_last or 0
            for e in d.get("entries", []):
                if live and int(e["id"]) <= last:
                    continue
                await self._ingest(e, live)
                last = max(last, int(e["id"]))
            self._feed_last = max(last, int(d.get("last_id") or 0))

    def _is_live_task(self, t: dict[str, Any] | None) -> bool:
        if not t:
            return False
        if t.get("kind") == "session":
            return t["state"] not in TERMINAL or bool(t.get("claimed_by"))
        return t["state"] in WORKING

    async def _ingest(self, e: dict[str, Any], live: bool) -> None:
        tid, ts, chunk = e["task_id"], int(e["ts"]), e["chunk"]
        agent, kind = split_source(e["source"])
        assigned = None
        if kind == "daemon":
            kind = daemon_kind(chunk)
            if chunk.startswith("routed to "):
                assigned = chunk[len("routed to "):].split()[0]
                self._task_agent[tid] = assigned
                self._routes[tid] = self._routes.get(tid, 0) + 1
                agent = assigned
            elif chunk.startswith("handoff to "):
                agent = chunk[len("handoff to "):].split(":")[0].strip()
            else:
                agent = self._task_agent.get(tid)
            show_as = None
        elif kind == "verifier" or agent is None:
            agent = self._task_agent.get(tid)
            show_as = agent
        else:
            self._task_agent[tid] = agent
            show_as = agent
            if kind == "status":
                kind = status_kind(chunk)

        task = self._task_by_id(tid)
        st = self._live.get(tid)
        if st is None and agent and self._is_live_task(task):
            st = await self._ensure_live(tid, agent, task)
        if st is not None:
            alog = self.query_one(f"#llog-{tid}", RichLog)
            if assigned:
                n = self._routes.get(tid, 0)
                att = f"{n}/{task['max_attempts'] if task else 3}" if n else ""
                alog.write(separator(agent or st.agent, tid, task["spec"] if task else "", att), expand=True,
                           scroll_end=self.follow)
            if kind in ("daemon", "note", "good", "bad", "handoff", "ask", "you", "warn"):
                alog.write(render(ts, kind, chunk, max_lines=8 if kind == "handoff" else None), expand=True,
                           scroll_end=self.follow)
            else:
                alog.write(render(ts, kind, chunk), expand=True, scroll_end=self.follow)
            st.lines += 1
            if kind == "msg" and agent:
                self._last_msg[(tid, agent)] = chunk

        hive = self.query_one("#hivelog", RichLog)
        if show_as is None and kind in ("daemon", "handoff", "good", "bad", "note", "ask", "you", "warn"):
            text = chunk
            if agent and kind in ("good", "bad") and not chunk.startswith("routed"):
                text = f"{chunk}   ({agent})"
            hive.write(render(ts, kind, text, agent=None, tid=tid, show_agent=True, show_task=True,
                              max_lines=8 if kind == "handoff" else None), expand=True, scroll_end=self.follow)
        elif kind in ("status", "info", "bad") and show_as:
            closing = chunk.startswith(("finished", "exited", "stopped", "pushed", "timed out"))
            if closing and (tid, show_as) in self._last_msg:
                said = self._last_msg.pop((tid, show_as))
                hive.write(render(ts, "msg", said, agent=show_as, tid=tid, show_agent=True, show_task=True,
                                  max_lines=4), expand=True, scroll_end=self.follow)
            hive.write(render(ts, kind, chunk, agent=show_as, tid=tid, show_agent=True, show_task=True),
                       expand=True, scroll_end=self.follow)

        if assigned and live:
            if self.auto_watch:
                self.query_one("#top", TabbedContent).active = f"live-{tid}"
            else:
                self.notify(f"▶ {assigned} picked up {tid[:8]}", timeout=4)

    async def _ensure_live(self, tid: str, agent: str, task: dict[str, Any] | None) -> LiveState:
        st = self._live.get(tid)
        if st is not None:
            return st
        kind = "session" if (task or {}).get("kind") == "session" else "task"
        st = LiveState(tid=tid, agent=agent, kind=kind, state=(task or {}).get("state", ""))
        if task:
            st.started = float(task.get("updated_at") or time.time())
        self._live[tid] = st
        await self.query_one("#top", TabbedContent).add_pane(LivePane(st))
        self._relabel(st)
        return st

    def _relabel(self, st: LiveState) -> None:
        task = self._task_by_id(st.tid)
        sess = _session_of(task)
        try:
            tc = self.query_one("#top", TabbedContent)
            tc.get_tab(st.pane_id).label = tab_label(st)
            self.query_one(f"#lhead-{st.tid}", Static).update(header_text(st, task, sess))
            if st.kind == "session":
                banner = self.query_one(f"#latt-{st.tid}", Static)
                banner.update(attention_text(st.attention))
                banner.display = bool(st.attention)
                inp = self.query_one(f"#lin-{st.tid}", Input)
                inp.display = st.state not in TERMINAL
        except Exception:
            pass

    async def _sync_live(self) -> None:
        tc = self.query_one("#top", TabbedContent)
        now = time.time()
        for t in self._tasks:
            if not self._is_live_task(t):
                continue
            agent = t.get("claimed_by") or _session_of(t).get("agent") or self._task_agent.get(t["id"])
            if not agent:
                continue
            st = self._live.get(t["id"])
            if st is None:
                st = await self._ensure_live(t["id"], agent, t)
            elif agent != st.agent:
                st.agent = agent
        for tid, st in list(self._live.items()):
            t = self._task_by_id(tid)
            if t is None:
                st.state = "gone"
                st.ended_at = st.ended_at or now
            else:
                st.state = t["state"]
                sess = _session_of(t)
                st.attention = sess.get("attention") if st.kind == "session" else None
                st.turn = sess.get("turn")
                st.lead = bool(sess.get("lead"))
                st.unread = int(sess.get("unread") or 0)
                if t["state"] in TERMINAL and st.ended_at is None:
                    st.ended_at = now
                if t["state"] not in TERMINAL:
                    st.ended_at = None
            self._relabel(st)
        stale = [st for st in self._live.values() if st.ended_at and now - st.ended_at > LIVE_KEEP_SECONDS
                 and tc.active != st.pane_id]
        finished = sorted([st for st in self._live.values() if st.ended_at and tc.active != st.pane_id],
                          key=lambda s: s.ended_at or 0)
        while len(self._live) - len(stale) > LIVE_MAX_TABS and finished:
            extra = finished.pop(0)
            if extra not in stale:
                stale.append(extra)
        for st in stale:
            self._live.pop(st.tid, None)
            try:
                await tc.remove_pane(st.pane_id)
            except Exception:
                pass

    def _active_live(self) -> LiveState | None:
        active = self.query_one("#top", TabbedContent).active
        if active.startswith("live-"):
            return self._live.get(active[5:])
        return None

    def _active_log(self) -> RichLog | None:
        active = self.query_one("#top", TabbedContent).active
        if active == "hive":
            return self.query_one("#hivelog", RichLog)
        if active.startswith("live-"):
            try:
                return self.query_one(f"#llog-{active[5:]}", RichLog)
            except Exception:
                return None
        return None

    # ── terminal preview (sessions on this machine) ────────────────────

    def _session_local(self, sess: dict[str, Any]) -> bool:
        return bool(self._tmux_ok and sess.get("tmux") and sess.get("host") == self._hostname)

    async def _refresh_terminal(self) -> None:
        st = self._active_live()
        if st is None or st.kind != "session":
            return
        try:
            term = self.query_one(f"#lterm-{st.tid}", Static)
        except Exception:
            return
        sess = _session_of(self._task_by_id(st.tid))
        if st.state in TERMINAL:
            term.update(Text(" session ended — the terminal is closed; the log on the left is the full record", style="dim"))
            return
        if not sess.get("host") or not sess.get("tmux"):
            term.update(Text(f" not started yet — waiting for a free {st.agent} lane on a worker", style="dim"))
            return
        if not self._session_local(sess):
            where = sess.get("host") or "another machine"
            term.update(Text(f" this session's terminal lives on {where}.\n run hm tui there to see and attach to it; "
                             "the event stream on the left works from anywhere.", style="dim"))
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux", "-L", TMUX_SOCKET, "capture-pane", "-e", "-p", "-t", str(sess["tmux"]), "-S", "-80",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        except Exception as e:
            term.update(Text(f" terminal preview unavailable: {e}", style="dim"))
            return
        screen = out.decode("utf-8", errors="replace").rstrip("\n")
        if screen == st.last_screen:
            return
        st.last_screen = screen
        lines = screen.splitlines()
        h = max(8, term.size.height or 30)
        shown = "\n".join(lines[-h:])
        text = Text.from_ansi(shown)
        term.update(text)

    # ── inbox ──────────────────────────────────────────────────────────

    def _render_inbox(self) -> None:
        table = self.query_one("#inboxtable", DataTable)
        keep = self._inbox_selected
        cursor = table.cursor_row
        table.clear()
        for it in self._inbox:
            att = it.get("attention") or {}
            mark, style = ATTENTION_STYLE.get(att.get("kind", ""), ("•", ""))
            what = att.get("summary") or ""
            opts = att.get("options") or []
            if opts:
                what += "   [" + " | ".join(f"{i}. {o}" for i, o in enumerate(opts, 1)) + "]"
            table.add_row(
                Text(mark, style=style),
                Text(att.get("kind", ""), style=style),
                Text(it["id"][:8], style="dim"),
                Text(it.get("agent") or "-", style=AGENT_STYLE.get(it.get("agent") or "", "")),
                Text(it.get("project", "")[:12]),
                Text(_age(att.get("since") or it.get("updated_at") or time.time()), style="dim"),
                Text(what[:160]),
                key=it["id"],
            )
        ids = [it["id"] for it in self._inbox]
        if keep in ids:
            table.move_cursor(row=ids.index(keep))
        elif ids:
            table.move_cursor(row=min(cursor, len(ids) - 1))
            self._inbox_selected = ids[min(cursor, len(ids) - 1)]
        else:
            self._inbox_selected = None
        try:
            self.query_one("#top", TabbedContent).get_tab("inbox").label = (
                Text.assemble(("Inbox ", ""), (f"{len(self._inbox)}", "bold black on yellow")) if self._inbox else Text("Inbox"))
        except Exception:
            pass

    @on(DataTable.RowHighlighted, "#inboxtable")
    def _inbox_highlighted(self, ev: DataTable.RowHighlighted) -> None:
        if ev.row_key is not None:
            self._inbox_selected = str(ev.row_key.value)

    def _notify_attention(self) -> None:
        first = not self._seen_attention
        for it in self._inbox:
            att = it.get("attention") or {}
            key = (it["id"], att.get("kind", ""), int(att.get("since") or 0))
            if key in self._seen_attention:
                continue
            self._seen_attention.add(key)
            if first:
                continue
            kind = att.get("kind")
            if kind in ("question", "permission", "usage_limit", "failed"):
                self.bell()
                sev = "error" if kind in ("permission", "failed") else "warning"
                self.notify(f"{it.get('agent') or ''} {it['id'][:8]} {kind}: {_first_line(att.get('summary'), 60)}   (n jumps to it)",
                            severity=sev, timeout=10)
            elif kind == "input" and it.get("kind") == "session":
                self.notify(f"{it.get('agent') or ''} {it['id'][:8]} is waiting for you", timeout=5)
        if len(self._seen_attention) > 2000:
            self._seen_attention = set(list(self._seen_attention)[-500:])

    # ── tabs ───────────────────────────────────────────────────────────

    def _top_ids(self) -> list[str]:
        return ["queue", "inbox", "hive"] + [st.pane_id for st in self._live.values()]

    def action_top(self, name: str) -> None:
        self.query_one("#top", TabbedContent).active = name

    def action_hive(self) -> None:
        self.action_top("hive")

    def action_inbox(self) -> None:
        self.action_top("inbox")
        self.query_one("#inboxtable", DataTable).focus()

    def _cycle(self, step: int) -> None:
        tc = self.query_one("#top", TabbedContent)
        ids = self._top_ids()
        i = ids.index(tc.active) if tc.active in ids else 0
        tc.active = ids[(i + step) % len(ids)]

    def action_top_next(self) -> None:
        self._cycle(1)

    def action_top_prev(self) -> None:
        self._cycle(-1)

    async def _goto_task(self, tid: str) -> None:
        t = self._task_by_id(tid)
        st = self._live.get(tid)
        if st is None and t is not None:
            agent = t.get("claimed_by") or _session_of(t).get("agent") or self._task_agent.get(tid)
            if agent and (self._is_live_task(t) or t.get("kind") == "session"):
                st = await self._ensure_live(tid, agent, t)
        if st is not None:
            self.query_one("#top", TabbedContent).active = st.pane_id
            return
        self.query_one("#top", TabbedContent).active = "queue"
        table = self.query_one("#tasks", DataTable)
        try:
            table.move_cursor(row=table.get_row_index(tid))
        except Exception:
            self.notify(f"{tid[:8]} isn't in the table (filtered out?)", severity="warning")

    async def action_next_attention(self) -> None:
        if not self._inbox:
            self.notify("nothing needs you right now")
            return
        cur = self._active_live()
        live_ids = [it["id"] for it in self._inbox if (it.get("attention") or {}).get("kind") != "failed"]
        ids = live_ids or [it["id"] for it in self._inbox]
        if cur and cur.tid in ids and len(ids) > 1:
            nxt = ids[(ids.index(cur.tid) + 1) % len(ids)]
        else:
            nxt = ids[0]
        await self._goto_task(nxt)

    def action_watch(self) -> None:
        tc = self.query_one("#top", TabbedContent)
        target = None
        if tc.active == "queue" and self.selected and self.selected in self._live:
            target = self.selected
        if not target:
            busy = [st for st in self._live.values() if not st.ended_at]
            if busy:
                target = max(busy, key=lambda st: st.started or 0).tid
        if not target:
            self.notify("no agent has picked up work yet", severity="warning")
            return
        tc.active = f"live-{target}"

    def action_toggle_auto_watch(self) -> None:
        self.auto_watch = not self.auto_watch
        self.notify(f"auto-watch {'on — the view jumps to each agent as it picks up work' if self.auto_watch else 'off'}")

    @on(TabbedContent.TabActivated, "#top")
    async def _top_activated(self, ev: TabbedContent.TabActivated) -> None:
        lg = self._active_log()
        if lg is not None and self.follow:
            lg.scroll_end(animate=False)
        st = self._active_live()
        if st is not None and st.kind == "session":
            if st.unread:
                st.unread = 0
                self._relabel(st)
                try:
                    await self.client.session_viewed(st.tid)
                except DaemonError:
                    pass
            await self._refresh_terminal()
        if ev.pane.id == "inbox":
            self.query_one("#inboxtable", DataTable).focus()
        elif ev.pane.id == "queue":
            self.query_one("#tasks", DataTable).focus()

    def _notify_transitions(self, new: list[dict[str, Any]]) -> None:
        if not self._tasks:
            return
        old = {t["id"]: t["state"] for t in self._tasks}
        for t in new:
            was = old.get(t["id"])
            if was is None or was == t["state"]:
                continue
            if t["state"] == "done":
                self.bell()
                self.notify(f"✓ {t['id'][:8]} done — {_first_line(t['spec'], 40)}", timeout=6)
            elif t["state"] == "failed":
                self.bell()
                self.notify(f"✗ {t['id'][:8]} failed — {_first_line(t['spec'], 40)}", severity="error", timeout=8)

    # ── queue table ────────────────────────────────────────────────────

    def _filtered(self) -> list[dict[str, Any]]:
        f = self.filter_text.strip()
        if not f:
            return self._tasks
        terms = f.split()
        out = []
        for t in self._tasks:
            ok = True
            for term in terms:
                if ":" in term:
                    k, v = term.split(":", 1)
                    val = {"state": t.get("state"), "agent": t.get("claimed_by") or _session_of(t).get("agent"),
                           "project": t.get("project"), "kind": t.get("kind")}.get(k)
                    if not val or v.lower() not in str(val).lower():
                        ok = False; break
                else:
                    hay = f"{t['id']} {t.get('project','')} {t.get('spec','')} {t.get('claimed_by','')}".lower()
                    if term.lower() not in hay:
                        ok = False; break
            if ok:
                out.append(t)
        return out

    def _render_table(self) -> None:
        table = self.query_one("#tasks", DataTable)
        rows = self._filtered()
        keep = self.selected
        cursor = table.cursor_row
        table.clear()
        for t in rows:
            st = t["state"]
            sess = _session_of(t)
            agent = t.get("claimed_by") or sess.get("agent")
            state_text = Text(st, style=STATE_STYLE.get(st, ""))
            att = sess.get("attention") if st not in TERMINAL else None
            if att:
                mark, style = ATTENTION_STYLE.get(att.get("kind", ""), ("•", ""))
                state_text.append(" " + mark, style=style)
            agent_text = Text()
            if t.get("kind") == "session":
                agent_text.append("★ " if sess.get("lead") else "⌨ ", style="yellow" if sess.get("lead") else "green")
            agent_text.append(agent or "-", style=AGENT_STYLE.get(agent or "", "dim"))
            table.add_row(
                Text(t["id"][:12], style="dim"),
                state_text,
                Text(f"{t['attempts']}/{t['max_attempts']}", style="dim" if t["attempts"] == 0 else ""),
                agent_text,
                Text(t["project"][:12]),
                Text(_age(t["updated_at"]), style="dim"),
                Text(_first_line(t["spec"])),
                key=t["id"],
            )
        if keep and any(r["id"] == keep for r in rows):
            try:
                table.move_cursor(row=table.get_row_index(keep))
            except Exception:
                pass
        elif rows:
            table.move_cursor(row=min(cursor, len(rows) - 1))
            self.selected = rows[min(cursor, len(rows) - 1)]["id"]
        else:
            self.selected = None

    @on(TabbedContent.TabActivated, "#detail")
    async def _tab_activated(self, ev: TabbedContent.TabActivated) -> None:
        if ev.pane.id == "diff" and self.selected:
            await self._refresh_diff(self.selected)

    @on(DataTable.RowHighlighted, "#tasks")
    async def _row_highlighted(self, ev: DataTable.RowHighlighted) -> None:
        if ev.row_key is None:
            return
        tid = str(ev.row_key.value)
        if tid != self.selected:
            self.selected = tid
            await self._refresh_detail(tid, force=True)

    async def _refresh_detail(self, tid: str, force: bool = False) -> None:
        try:
            d = await self.client.task(tid)
        except DaemonError as e:
            self.query_one("#ov", Static).update(Text(str(e), style="red"))
            return
        prev = self._detail_cache.get(tid, {}).get("task", {}).get("state")
        self._detail_cache[tid] = d
        self._render_overview(d)
        await self._refresh_log(tid, reset=force or self._log_task != tid)
        state_changed = prev is not None and prev != d["task"]["state"]
        if force or self._log_task != tid or state_changed:
            self._log_task = tid
            await self._refresh_diff(tid, d)

    def _render_overview(self, d: dict[str, Any]) -> None:
        t, c, atts = d["task"], d.get("classification"), d.get("attempts", [])
        sess = _session_of(t)
        out = Text()
        out.append(f"{t['id']}\n", style="bold")
        if sess:
            out.append("session   ", style="dim")
            out.append(f"{sess.get('agent')} ", style=AGENT_STYLE.get(sess.get("agent") or "", ""))
            out.append(f"{sess.get('name') or ''}  {sess.get('permission_mode')}  ", style="dim")
            out.append(f"{sess.get('turn') or ''}", style="cyan")
            if sess.get("host"):
                out.append(f"  on {sess['host']} tmux {sess.get('tmux') or ''}", style="dim")
            out.append("\n")
            att = sess.get("attention")
            if att and t["state"] not in TERMINAL:
                out.append(attention_text(att))
                out.append("\n")
        out.append("state     ", style="dim"); out.append(f"{t['state']}\n", style=STATE_STYLE.get(t["state"], ""))
        out.append("project   ", style="dim"); out.append(f"{t['project']}   ")
        out.append("base ", style="dim"); out.append(f"{t['base_ref']}   ")
        out.append("attempts ", style="dim"); out.append(f"{t['attempts']}/{t['max_attempts']}\n")
        if t.get("worktree"):
            out.append("worktree  ", style="dim"); out.append(f"{t['worktree']}\n")
        if t["state"] == "done":
            out.append("branch    ", style="dim"); out.append(f"hiveswarm/{t['id']}  ", style="green")
            out.append("press m to merge\n", style="dim")
        out.append("\nspec\n", style="bold")
        out.append(t["spec"].strip() + "\n")
        out.append("\nacceptance\n", style="bold")
        out.append((t.get("acceptance") or "(none — soft verify)") + "\n", style="cyan" if t.get("acceptance") else "dim")
        if c:
            out.append("\nclassification\n", style="bold")
            out.append(f"  {c['task_type']} ", style="yellow"); out.append(f"(conf {c['type_conf']:.2f})   ", style="dim")
            out.append(f"difficulty {c['difficulty']:.1f} ", style="yellow"); out.append(f"(conf {c['difficulty_conf']:.2f})\n", style="dim")
            out.append(f"  multistep {c.get('is_multistep') or 0:.2f}   tools {c.get('needs_tools') or 0:.2f}   ", style="dim")
            out.append(f"via {c['source']}\n", style="yellow" if c["source"] == "fallback" else "dim")
        if atts:
            out.append("\nattempts\n", style="bold")
            for a in atts:
                oc = a.get("outcome") or "…"
                style = "green" if oc == "pass" else ("red" if oc in ("fail", "error", "timeout") else "dim")
                out.append(f"  {a['agent']:<13}", style=AGENT_STYLE.get(a["agent"], ""))
                out.append(f"{oc:<10}", style=style)
                out.append(f"{(a.get('wall_seconds') or 0):>6.0f}s   ", style="dim")
                if a.get("tokens_in"):
                    out.append(f"tok {a['tokens_in']}/{a.get('tokens_out')}   ", style="dim")
                if a.get("diff_stat"):
                    out.append(_first_line(a["diff_stat"], 40), style="dim")
                out.append("\n")
        self.query_one("#ov", Static).update(out)

    async def _refresh_log(self, tid: str, reset: bool = False) -> None:
        log = self.query_one("#loglog", RichLog)
        if reset:
            log.clear()
            self._log_seq[tid] = 0
        try:
            d = await self.client.log(tid, since=self._log_seq.get(tid, 0))
        except DaemonError:
            return
        for e in d.get("entries", []):
            agent, kind = split_source(e["source"])
            if kind == "daemon":
                kind = daemon_kind(e["chunk"])
            elif kind == "status":
                kind = status_kind(e["chunk"])
            log.write(render(int(e["ts"]), kind, e["chunk"], agent=agent, show_agent=True), expand=True, scroll_end=self.follow)
        if d.get("entries"):
            self._log_seq[tid] = d["last_seq"]

    async def _refresh_diff(self, tid: str, d: dict[str, Any] | None = None) -> None:
        dl = self.query_one("#difflog", RichLog)
        dl.clear()
        t = (d or self._detail_cache.get(tid, {})).get("task", {})
        if t.get("state") not in ("done", "verifying", "failed"):
            dl.write(Text("no branch yet — diff appears once an attempt has committed", style="dim"))
            return
        try:
            r = await self.client.diff(tid)
        except DaemonError as e:
            dl.write(Text(str(e), style="red"))
            return
        if not r.get("ok"):
            dl.write(Text(r.get("error") or "no diff", style="dim"))
            return
        dl.write(Text(r.get("stat", ""), style="bold"))
        for line in (r.get("diff") or "").splitlines():
            if line.startswith("+++") or line.startswith("---"):
                dl.write(Text(line, style="bold"))
            elif line.startswith("@@"):
                dl.write(Text(line, style="cyan"))
            elif line.startswith("+"):
                dl.write(Text(line, style="green"))
            elif line.startswith("-"):
                dl.write(Text(line, style="red"))
            else:
                dl.write(Text(line))

    # ── which task do actions apply to ─────────────────────────────────

    def _current(self) -> dict[str, Any] | None:
        active = self.query_one("#top", TabbedContent).active
        if active.startswith("live-"):
            t = self._task_by_id(active[5:])
            self._action_tid = t["id"] if t else None
            return t
        if active == "inbox":
            t = self._task_by_id(self._inbox_selected)
            self._action_tid = t["id"] if t else None
            return t
        t = self._task_by_id(self.selected)
        self._action_tid = t["id"] if t else None
        return t

    def _current_session(self, need_attention: tuple[str, ...] | None = None) -> tuple[dict[str, Any], dict[str, Any]] | None:
        t = self._current()
        if not t or t.get("kind") != "session":
            self.notify("select a session first (Inbox, a ⌨ tab, or a ⌨ row in the queue)", severity="warning")
            return None
        if t["state"] in TERMINAL:
            self.notify("that session has ended — r reopens it", severity="warning")
            return None
        sess = _session_of(t)
        if need_attention is not None:
            kind = (sess.get("attention") or {}).get("kind")
            if kind not in need_attention:
                self.notify(f"{t['id'][:8]} isn't asking for that right now" + (f" (it's {kind})" if kind else ""),
                            severity="warning")
                return None
        return t, sess

    # ── session actions ────────────────────────────────────────────────

    async def action_approve(self) -> None:
        cs = self._current_session(("permission",))
        if not cs:
            return
        t, _ = cs
        try:
            await self.client.session_answer(t["id"], "approve")
            self.notify(f"approved {t['id'][:8]}")
        except DaemonError as e:
            self.notify(str(e), severity="error")
        await self._tick()

    async def action_deny(self) -> None:
        cs = self._current_session(("permission",))
        if not cs:
            return
        t, _ = cs
        try:
            await self.client.session_answer(t["id"], "deny")
            self.notify(f"denied {t['id'][:8]} — it will look for another way or ask you")
        except DaemonError as e:
            self.notify(str(e), severity="error")
        await self._tick()

    async def action_digit(self, n: int) -> None:
        active = self.query_one("#top", TabbedContent).active
        if active == "queue":
            if n in (1, 2, 3):
                await self.action_tab({1: "overview", 2: "log", 3: "diff"}[n])
            return
        cs = self._current_session(("question",))
        if not cs:
            return
        t, sess = cs
        opts = (sess.get("attention") or {}).get("options") or []
        if not opts or n > len(opts):
            self.notify(f"no option {n}; type your answer with t", severity="warning")
            return
        try:
            await self.client.session_answer(t["id"], str(n))
            self.notify(f"answered {t['id'][:8]}: {opts[n - 1]}")
        except DaemonError as e:
            self.notify(str(e), severity="error")
        await self._tick()

    async def action_type_message(self) -> None:
        t = self._current()
        if not t or t.get("kind") != "session" or t["state"] in TERMINAL:
            self.notify("select a live session first", severity="warning")
            return
        await self._goto_task(t["id"])
        try:
            self.query_one(f"#lin-{t['id']}", Input).focus()
        except Exception:
            pass

    @on(Input.Submitted, ".sessioninput")
    async def _session_input(self, ev: Input.Submitted) -> None:
        tid = (ev.input.id or "")[4:]
        text = ev.value.strip()
        if not text:
            return
        t = self._task_by_id(tid)
        sess = _session_of(t)
        kind = (sess.get("attention") or {}).get("kind")
        try:
            if kind in ("question", "permission"):
                await self.client.session_answer(tid, "text", text)
                self.notify("answered" if kind == "question" else "denied with your message")
            else:
                await self.client.session_send(tid, text=text)
                self.notify("sent")
            ev.input.value = ""
        except DaemonError as e:
            self.notify(str(e), severity="error")
        await self._tick()

    def action_escape(self) -> None:
        f = self.query_one("#filter", Input)
        if f.display and f.has_focus:
            self.action_clear_filter()
            return
        focused = self.focused
        if isinstance(focused, Input) and focused.has_class("sessioninput"):
            self.set_focus(None)
            return
        if f.display:
            self.action_clear_filter()

    def action_continue_session(self) -> None:
        cs = self._current_session()
        if not cs:
            return
        t, sess = cs
        self.push_screen(ContinueScreen(t, self._session_agents, sess.get("agent")), callback=self._on_continue)

    async def _on_continue(self, res: dict[str, Any] | None) -> None:
        if not res or not self._action_tid:
            return
        try:
            r = await self.client.session_continue(self._action_tid, res["agent"], res.get("permission_mode"))
            if r.get("pending"):
                self.notify(f"saving {self._action_tid[:8]}'s work, then {res['agent']} takes over", timeout=6)
            else:
                self.notify(f"continued as {str(r.get('id', ''))[:8]} on {res['agent']}", timeout=6)
        except DaemonError as e:
            self.notify(str(e), severity="error")
        await self._tick()

    def action_finish_session(self) -> None:
        cs = self._current_session()
        if not cs:
            return
        t, _ = cs
        self.push_screen(ConfirmScreen(f"Finish session {t['id'][:8]}? Its worktree is committed, pushed, and verified; "
                                       "the agent's terminal closes."), callback=self._on_finish)

    async def _on_finish(self, yes: bool) -> None:
        if not yes or not self._action_tid:
            return
        try:
            await self.client.session_finish(self._action_tid)
            self.notify("finishing — the branch appears in the queue when verification is done")
        except DaemonError as e:
            self.notify(str(e), severity="error")
        await self._tick()

    async def action_primary(self) -> None:
        active = self.query_one("#top", TabbedContent).active
        if active == "inbox":
            if self._inbox_selected:
                await self._goto_task(self._inbox_selected)
            return
        if active.startswith("live-"):
            self.action_attach()
            return
        t = self._task_by_id(self.selected)
        if t and t.get("kind") == "session" and self._is_live_task(t):
            await self._goto_task(t["id"])
            return
        self.query_one("#detail", TabbedContent).active = "log"

    def action_attach(self) -> None:
        st = self._active_live()
        if st is None or st.kind != "session":
            self.notify("open a session tab first", severity="warning")
            return
        sess = _session_of(self._task_by_id(st.tid))
        if st.state in TERMINAL:
            self.notify("that session has ended", severity="warning")
            return
        if not self._session_local(sess):
            self.notify(f"this session's terminal is on {sess.get('host') or 'another machine'}; run hm tui there to attach",
                        severity="warning", timeout=8)
            return
        env = dict(os.environ)
        env.pop("TMUX", None)
        with self.suspend():
            subprocess.run(["tmux", "-L", TMUX_SOCKET, "select-window", "-t", str(sess["tmux"])], env=env)
            subprocess.run(["tmux", "-L", TMUX_SOCKET, "attach-session", "-t", TMUX_SESSION], env=env)
        self.notify("back in Hiveswarm (prefix+d detaches from an agent's terminal)", timeout=4)

    # ── task actions ───────────────────────────────────────────────────

    async def _enqueue_many(self, items: list[dict[str, Any]]) -> None:
        n = 0
        for it in items:
            try:
                await self.client.add(it["project"], it["spec"], it.get("acceptance"))
                n += 1
            except DaemonError as e:
                self.notify(f"enqueue failed: {e}", severity="error")
        if n:
            self.notify(f"enqueued {n} task{'s' if n != 1 else ''}")
            await self._tick()

    async def _open_sessions(self, project: str, items: list[dict[str, Any]], agent: str, perm: str, count: int = 1) -> None:
        n = 0
        for it in items:
            try:
                r = await self.client.new_session(project, it["spec"], it.get("acceptance"), agent, perm, count)
                n += len(r.get("ids") or [r.get("id")])
            except DaemonError as e:
                self.notify(f"couldn't open session: {e}", severity="error", timeout=10)
        if n:
            self.notify(f"opened {n} interactive session{'s' if n != 1 else ''} on {agent} — tabs appear as they start",
                        timeout=6)
            await self._tick()

    async def action_add(self) -> None:
        await self._load_projects()
        if not self._projects:
            self.notify("No projects yet — press g and choose + New project…", severity="warning")
            return
        cur = self._current()
        self.push_screen(AddTaskScreen(self._projects, self._templates, cur["project"] if cur else None),
                         callback=self._on_add)

    async def _on_add(self, res: dict[str, Any] | None) -> None:
        if res:
            await self._enqueue_many([res])

    async def action_decompose(self) -> None:
        await self._load_projects()
        cur = self._current()
        self.push_screen(DecomposeScreen(self._projects, self._templates, cur["project"] if cur else None,
                                         session_agents=self._session_agents),
                         callback=self._on_decompose)

    async def _on_decompose(self, res: dict[str, Any] | None) -> None:
        if res:
            self._run_goal(res)

    @work(group="goal")
    async def _run_goal(self, res: dict[str, Any]) -> None:
        proj = res.get("project")
        created = False
        if res.get("new_name"):
            try:
                r = await self.client.create_project(res["new_name"])
            except DaemonError as e:
                self.notify(f"couldn't create project: {e}", severity="error", timeout=10)
                return
            proj = r["name"]
            created = True
            await self._load_projects()
            self.notify(f"created project {proj} at {r['repo_path']} on the hub", timeout=6)
        if not proj:
            return
        run = res.get("run", "tasks")
        agent = res.get("agent") or "claude_code"
        perm = res.get("permission_mode") or "auto"
        if res["mode"] == "session":
            await self._open_sessions(proj, [{"spec": res["goal"], "acceptance": None}], agent, perm,
                                      int(res.get("count") or 1))
            return
        if res["mode"] == "lead":
            try:
                r = await self.client.new_session(proj, res["goal"], None, "claude_code", perm, 1, lead=True)
                self.notify(f"lead session {r['id'][:8]} starting — it plans and dispatches the swarm; watch its tab",
                            timeout=8)
            except DaemonError as e:
                self.notify(f"couldn't start the lead: {e}", severity="error", timeout=10)
            await self._tick()
            return
        if res["mode"] == "review":
            items = [{**t, "project": proj} for t in res["tasks"]]
            if run == "swarm":
                await self._open_sessions(proj, items, agent, perm)
            else:
                await self._enqueue_many(items)
            return
        goal = res["goal"]
        self.notify("planning with Claude Code… the agents start as soon as the plan is ready", timeout=8)
        repo = self._projects.get(proj, {}).get("repo_path", "")
        tasks, err = await _decompose.decompose(goal, proj, repo, self._templates.get(proj, []), new_repo=created)
        if created and tasks:
            tasks = tasks[:1]
            self.notify("new project: one agent builds the first version (parallel agents on an empty repo would clash)",
                        timeout=8)
        if err or not tasks:
            self.notify(f"planning failed ({(err or 'no tasks')[:100]}); sending the goal as one {'session' if run == 'swarm' else 'task'} instead",
                        severity="warning", timeout=10)
            tasks = [{"spec": goal, "acceptance": None}]
        items = [{"project": proj, "spec": t["spec"], "acceptance": t.get("acceptance")} for t in tasks]
        if run == "swarm":
            await self._open_sessions(proj, items, agent, perm)
        else:
            await self._enqueue_many(items)

    def action_retry(self) -> None:
        t = self._current()
        if not t:
            return
        if t["state"] not in ("failed", "abandoned", "blocked", "done"):
            self.notify(f"can't retry a task in state {t['state']}", severity="warning")
            return
        d = self._detail_cache.get(t["id"], {})
        last = ""
        for a in reversed(d.get("attempts", [])):
            if a.get("verifier_log"):
                last = a["verifier_log"]
                break
        self.push_screen(RetryScreen(t, last), callback=self._on_retry)

    async def _on_retry(self, res: dict[str, Any] | None) -> None:
        if res is None or not self._action_tid:
            return
        try:
            r = await self.client.retry(self._action_tid, res.get("spec"), res.get("acceptance"))
            self.notify("reopened" if r.get("ok") else r.get("reason", "retry refused"),
                        severity="information" if r.get("ok") else "warning")
        except DaemonError as e:
            self.notify(str(e), severity="error")
        await self._tick()

    def action_cancel_task(self) -> None:
        t = self._current()
        if not t:
            return
        if t["state"] in ("done", "failed", "abandoned"):
            self.notify("already terminal", severity="warning")
            return
        what = "session" if t.get("kind") == "session" else "task"
        self.push_screen(ConfirmScreen(f"Cancel {what} {t['id'][:12]}? This kills the running agent.", danger=True),
                         callback=self._on_cancel)

    async def _on_cancel(self, yes: bool) -> None:
        if not yes or not self._action_tid:
            return
        try:
            r = await self.client.cancel(self._action_tid)
            self.notify("cancelled" if r.get("ok") else r.get("reason", ""))
        except DaemonError as e:
            self.notify(str(e), severity="error")
        await self._tick()

    def action_merge(self) -> None:
        t = self._current()
        if not t:
            return
        if t["state"] != "done":
            self.notify("only done tasks can be merged", severity="warning")
            return
        self.push_screen(ConfirmScreen(f"Merge hiveswarm/{t['id'][:12]} into the repo's current branch?"),
                         callback=self._on_merge)

    async def _on_merge(self, yes: bool) -> None:
        if not yes or not self._action_tid:
            return
        try:
            r = await self.client.merge(self._action_tid)
            if r.get("ok"):
                self.notify(f"merged into {r.get('into')}")
            else:
                self.notify(r.get("error") or r.get("reason") or "merge failed", severity="error", timeout=10)
        except DaemonError as e:
            self.notify(str(e), severity="error")
        await self._tick()

    def action_delete(self) -> None:
        t = self._current()
        if not t:
            return
        self.push_screen(ConfirmScreen(f"Delete {t['id'][:12]} and its worktree? This cannot be undone.", danger=True),
                         callback=self._on_delete)

    async def _on_delete(self, yes: bool) -> None:
        if not yes or not self._action_tid:
            return
        try:
            await self.client.delete(self._action_tid)
            self.notify("deleted")
            if self.selected == self._action_tid:
                self.selected = None
        except DaemonError as e:
            self.notify(str(e), severity="error")
        await self._tick()

    def action_toggle_follow(self) -> None:
        self.follow = not self.follow
        self.notify(f"follow {'on' if self.follow else 'off'}")
        if self.follow:
            self.query_one("#loglog", RichLog).scroll_end(animate=False)
            lg = self._active_log()
            if lg is not None:
                lg.scroll_end(animate=False)

    async def action_tab(self, name: str) -> None:
        self.query_one("#detail", TabbedContent).active = name
        if name == "diff" and self.selected:
            await self._refresh_diff(self.selected)

    def action_filter(self) -> None:
        self.query_one("#top", TabbedContent).active = "queue"
        f = self.query_one("#filter", Input)
        f.display = True
        f.focus()

    def action_clear_filter(self) -> None:
        f = self.query_one("#filter", Input)
        if f.display:
            f.value = ""
            f.display = False
            self.filter_text = ""
            self._render_table()
            self.query_one("#tasks", DataTable).focus()

    @on(Input.Submitted, "#filter")
    def _filter_submitted(self, ev: Input.Submitted) -> None:
        self.filter_text = ev.value
        self._render_table()
        self.query_one("#tasks", DataTable).focus()

    @on(Input.Changed, "#filter")
    def _filter_changed(self, ev: Input.Changed) -> None:
        self.filter_text = ev.value
        self._render_table()

    def action_cursor_down(self) -> None:
        active = self.query_one("#top", TabbedContent).active
        if active == "inbox":
            self.query_one("#inboxtable", DataTable).action_cursor_down()
            return
        lg = self._active_log()
        if lg is not None:
            lg.scroll_down(animate=False)
            return
        self.query_one("#tasks", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        active = self.query_one("#top", TabbedContent).active
        if active == "inbox":
            self.query_one("#inboxtable", DataTable).action_cursor_up()
            return
        lg = self._active_log()
        if lg is not None:
            lg.scroll_up(animate=False)
            return
        self.query_one("#tasks", DataTable).action_cursor_up()

    async def action_refresh(self) -> None:
        await self._load_projects()
        await self._tick()
        if self.selected:
            await self._refresh_detail(self.selected, force=True)
        self.notify("refreshed")

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    @work(exclusive=True)
    async def action_stats(self) -> None:
        from .views import StatsScreen
        try:
            rows = await self.client.stats()
        except DaemonError as e:
            self.notify(str(e), severity="error")
            return
        self.push_screen(StatsScreen(rows))

    @work(exclusive=True)
    async def action_agents(self) -> None:
        from .views import AgentsScreen
        try:
            rows = await self.client.agents()
        except DaemonError as e:
            self.notify(str(e), severity="error")
            return
        self.push_screen(AgentsScreen(rows))

    async def on_unmount(self) -> None:
        await self.client.close()


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(prog="hm tui")
    p.add_argument("--url", default=None, help="daemon URL (default: $HIVESWARM_URL)")
    p.add_argument("--poll", type=float, default=2.0, help="refresh interval in seconds")
    a = p.parse_args()
    HiveswarmApp(url=a.url, poll=a.poll).run()


if __name__ == "__main__":
    main()
