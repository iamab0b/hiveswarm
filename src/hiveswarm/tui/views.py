from __future__ import annotations

import json
import time
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Label, Static

TYPES = ["refactor", "test_write", "bug_fix", "feature", "docs", "review", "investigate", "frontend"]


def _cell(alpha: float, beta: float, n: int) -> Text:
    if n == 0:
        return Text("  ·  ", style="dim")
    p = alpha / (alpha + beta)
    if n < 3:
        style = "dim"
    elif p >= 0.7:
        style = "bold green"
    elif p >= 0.4:
        style = "yellow"
    else:
        style = "bold red"
    return Text(f"{p:.2f}", style=style) + Text(f"/{n}", style="dim")


class StatsScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close"), Binding("q", "close", "Close"), Binding("s", "close", "Close")]

    def __init__(self, rows: list[dict[str, Any]]):
        super().__init__()
        self.rows = rows

    def compose(self) -> ComposeResult:
        with Vertical(id="modal", classes="wide-auto"):
            yield Label("Routing table — posterior p(success) / samples, per agent × task type × difficulty band", classes="title")
            yield Static("green ≥ 0.70   yellow ≥ 0.40   red < 0.40   dim = fewer than 3 samples   · = never tried", classes="dim")
            yield DataTable(id="grid", zebra_stripes=True)
            yield Static("", id="summary", classes="dim")

    def on_mount(self) -> None:
        grid = self.query_one("#grid", DataTable)
        grid.add_column("agent", key="agent")
        grid.add_column("band", key="band")
        for t in TYPES:
            grid.add_column(t[:10], key=t)
        by: dict[tuple[str, int], dict[str, tuple[float, float, int]]] = {}
        agents: set[str] = set()
        for r in self.rows:
            agents.add(r["agent"])
            by.setdefault((r["agent"], r["diff_band"]), {})[r["task_type"]] = (r["alpha"], r["beta"], r["n"])
        total = 0
        for agent in sorted(agents):
            for band in (1, 2, 3, 4):
                cells = by.get((agent, band), {})
                if not cells:
                    continue
                row = [Text(agent, style="bold"), Text(str(band))]
                for t in TYPES:
                    a, b, n = cells.get(t, (1.0, 1.0, 0))
                    total += n
                    row.append(_cell(a, b, n))
                grid.add_row(*row)
        if not agents:
            grid.add_row(Text("no attempts recorded yet — the table fills in as tasks complete", style="dim"))
        self.query_one("#summary", Static).update(f"{total} verified attempts across {len(agents)} agents. "
                                                  "Phase 3 routing samples from these posteriors.")

    def action_close(self) -> None:
        self.dismiss(None)


class AgentsScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close"), Binding("q", "close", "Close"), Binding("A", "close", "Close")]

    def __init__(self, rows: list[dict[str, Any]]):
        super().__init__()
        self.rows = rows

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Label("Agents", classes="title")
            yield DataTable(id="agents", zebra_stripes=True)
            yield Static("Laptop lanes register when hiveswarm-worker starts and go stale 2 minutes after its last poll. "
                         "Hub-local lanes (prime_agent, local_direct) don't register — the router checks them directly.", classes="dim")

    def on_mount(self) -> None:
        t = self.query_one("#agents", DataTable)
        t.add_columns("agent", "host", "alive", "busy", "last seen", "capabilities")
        now = int(time.time())
        for r in sorted(self.rows, key=lambda x: (not x.get("alive"), x["agent_id"])):
            caps = r.get("capabilities")
            if isinstance(caps, str):
                try:
                    caps = json.loads(caps)
                except Exception:
                    caps = [caps]
            age = now - int(r.get("last_seen") or now)
            t.add_row(
                Text(r["agent_id"], style="bold"),
                Text(r.get("host") or "-"),
                Text("● alive", style="green") if r.get("alive") else Text("○ stale", style="dim red"),
                Text(f"{r.get('busy', 0)}/{r.get('capacity', 1)}", style="cyan" if r.get("busy") else "dim"),
                Text(f"{age}s ago" if age < 3600 else f"{age // 3600}h ago", style="dim"),
                Text(", ".join(caps or [])),
            )
        if not self.rows:
            t.add_row(Text("no workers have registered — run hiveswarm-worker (or hm up)", style="dim"))

    def action_close(self) -> None:
        self.dismiss(None)
