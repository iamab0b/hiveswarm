from __future__ import annotations

import re
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static, TextArea

from . import decompose as _decompose


class AddTaskScreen(ModalScreen[dict[str, Any] | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "submit", "Enqueue")]

    def __init__(self, projects: dict[str, Any], templates: dict[str, list[str]], default_project: str | None = None):
        super().__init__()
        self.projects = projects
        self.templates = templates
        self.default_project = default_project or (next(iter(projects)) if projects else None)

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Label("Enqueue a task", classes="title")
            yield Label("Project")
            yield Select([(p, p) for p in self.projects], value=self.default_project, id="project", allow_blank=False)
            yield Label("Spec — what should the agent do? Be concrete: files, functions, expected behavior.")
            yield TextArea(id="spec")
            yield Label("Acceptance — a shell command that exits 0 only when the task is correctly done")
            yield Input(placeholder="python3 -m pytest -q", id="acceptance")
            tmpl = self.templates.get(self.default_project or "", [])
            if tmpl:
                with Horizontal(id="templates"):
                    yield Label("Templates:", classes="dim")
                    for i, t in enumerate(tmpl[:4]):
                        yield Button(t, id=f"tmpl-{i}", variant="default", classes="tmpl")
            with Horizontal(classes="buttons"):
                yield Button("Enqueue  ctrl+s", variant="primary", id="ok")
                yield Button("Cancel  esc", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#spec", TextArea).focus()

    @on(Select.Changed, "#project")
    def _project_changed(self, ev: Select.Changed) -> None:
        tmpl = self.templates.get(str(ev.value), [])
        for i, b in enumerate(self.query(".tmpl")):
            if i < len(tmpl):
                b.label = tmpl[i]
                b.display = True
            else:
                b.display = False

    @on(Button.Pressed, ".tmpl")
    def _use_template(self, ev: Button.Pressed) -> None:
        self.query_one("#acceptance", Input).value = str(ev.button.label)

    @on(Button.Pressed, "#ok")
    def action_submit(self) -> None:
        spec = self.query_one("#spec", TextArea).text.strip()
        if not spec:
            self.notify("Spec is empty", severity="warning")
            return
        acc = self.query_one("#acceptance", Input).value.strip() or None
        proj = str(self.query_one("#project", Select).value)
        self.dismiss({"project": proj, "spec": spec, "acceptance": acc})

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class RetryScreen(ModalScreen[dict[str, Any] | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "submit", "Retry")]

    def __init__(self, task: dict[str, Any], last_error: str = ""):
        super().__init__()
        self.task_data = task
        self.last_error = last_error

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Label(f"Retry {self.task_data['id']} — amend the spec if the last attempt missed something", classes="title")
            if self.last_error:
                yield Static(self.last_error[-600:], classes="errbox")
            yield Label("Spec")
            yield TextArea(self.task_data["spec"], id="spec")
            yield Label("Acceptance")
            yield Input(value=self.task_data.get("acceptance") or "", id="acceptance")
            with Horizontal(classes="buttons"):
                yield Button("Retry  ctrl+s", variant="primary", id="ok")
                yield Button("Cancel  esc", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#spec", TextArea).focus()

    @on(Button.Pressed, "#ok")
    def action_submit(self) -> None:
        spec = self.query_one("#spec", TextArea).text.strip()
        acc = self.query_one("#acceptance", Input).value.strip() or None
        changed = spec != self.task_data["spec"] or acc != (self.task_data.get("acceptance") or None)
        self.dismiss({"spec": spec if changed else None, "acceptance": acc if changed else None})

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


NEW_PROJECT = "__new__"
_NAME_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


RUN_MODES = [
    ("Tasks — plan it, then agents work headless (fastest, fully automatic)", "tasks"),
    ("Lead — a Claude Code session commands the swarm with Hiveswarm tools; you watch and steer it", "lead"),
    ("Swarm — plan it, then one interactive session per subtask (watch & steer each)", "swarm"),
    ("Session — one interactive session with this goal as the prompt", "session"),
]
PERMISSION_MODES = [
    ("auto — approves low-risk actions, asks you about risky ones (recommended)", "auto"),
    ("acceptEdits — edits allowed, every command asks you", "acceptEdits"),
    ("bypass — never asks (sandbox only)", "bypass"),
]


class DecomposeScreen(ModalScreen[dict[str, Any] | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "submit", "Start")]

    def __init__(self, projects: dict[str, Any], templates: dict[str, list[str]], default_project: str | None = None,
                 session_agents: list[str] | None = None, default_mode: str = "tasks"):
        super().__init__()
        self.projects = projects
        self.templates = templates
        self.default_project = default_project if default_project in projects else (next(iter(projects)) if projects else NEW_PROJECT)
        self.proposed: list[dict[str, Any]] = []
        self.session_agents = session_agents or ["claude_code"]
        self.default_mode = default_mode

    def compose(self) -> ComposeResult:
        options = [(p, p) for p in self.projects] + [("+ New project…", NEW_PROJECT)]
        with Vertical(id="modal", classes="wide-auto"):
            yield Label("New goal — say what you want; Hiveswarm plans it and the agents start", classes="title")
            with Horizontal():
                yield Label("Project ")
                yield Select(options, value=self.default_project, id="project", allow_blank=False)
            yield Input(placeholder="name for the new project (letters, digits, - and _)", id="newname")
            yield Label("Goal")
            yield TextArea(id="goal")
            with Horizontal():
                yield Label("Run as  ")
                yield Select(RUN_MODES, value=self.default_mode, id="mode", allow_blank=False)
            with Horizontal(id="sessopts"):
                yield Label("Agent ")
                yield Select([(a, a) for a in self.session_agents], value=self.session_agents[0], id="agent",
                             allow_blank=False)
                yield Label(" Permissions ")
                yield Select(PERMISSION_MODES, value="auto", id="perm", allow_blank=False)
                yield Label(" Copies ")
                yield Input(value="1", id="count", classes="tiny")
            yield Checkbox("Let me review the plan before the agents start", value=False, id="review")
            with Horizontal(classes="buttons"):
                yield Button("Start  ctrl+s", variant="primary", id="ok")
                yield Button("Cancel  esc", id="cancel")
            yield Static("", id="status", classes="dim")
            yield VerticalScroll(id="proposals")

    def on_mount(self) -> None:
        self.query_one("#newname", Input).display = self.default_project == NEW_PROJECT
        self.query_one("#proposals", VerticalScroll).display = False
        self._mode_ui(self.default_mode)
        if self.default_project == NEW_PROJECT:
            self.query_one("#newname", Input).focus()
        else:
            self.query_one("#goal", TextArea).focus()

    def _mode_ui(self, mode: str) -> None:
        self.query_one("#sessopts", Horizontal).display = mode in ("swarm", "session", "lead")
        self.query_one("#count", Input).display = mode == "session"
        self.query_one("#agent", Select).disabled = mode == "lead"
        if mode == "lead":
            self.query_one("#agent", Select).value = "claude_code" if "claude_code" in self.session_agents else self.session_agents[0]
        self.query_one("#review", Checkbox).display = mode not in ("session", "lead")
        label = {"tasks": "Start  ctrl+s", "swarm": "Plan & open sessions  ctrl+s", "session": "Open session  ctrl+s",
                 "lead": "Start the lead  ctrl+s"}
        self.query_one("#ok", Button).label = label.get(mode, "Start  ctrl+s")

    @on(Select.Changed, "#mode")
    def _mode_changed(self, ev: Select.Changed) -> None:
        self._mode_ui(str(ev.value))

    @on(Select.Changed, "#project")
    def _project_changed(self, ev: Select.Changed) -> None:
        is_new = ev.value == NEW_PROJECT
        box = self.query_one("#newname", Input)
        box.display = is_new
        if is_new:
            box.focus()

    @on(Checkbox.Changed, "#review")
    def _review_changed(self, ev: Checkbox.Changed) -> None:
        if not ev.value:
            self.proposed = []
            self.query_one("#proposals", VerticalScroll).display = False
            self.query_one("#ok", Button).label = "Start  ctrl+s"
            self.query_one("#status", Static).update("")

    def _target(self) -> tuple[str | None, str | None, str] | None:
        goal = self.query_one("#goal", TextArea).text.strip()
        if not goal:
            self.notify("Goal is empty", severity="warning")
            return None
        proj = str(self.query_one("#project", Select).value)
        new_name = None
        if proj == NEW_PROJECT:
            new_name = self.query_one("#newname", Input).value.strip()
            if not _NAME_OK.match(new_name):
                self.notify("Project name: letters, digits, - and _ only", severity="warning")
                return None
            if new_name in self.projects:
                self.notify(f"'{new_name}' already exists — pick it from the list instead", severity="warning")
                return None
            proj = None
        return proj, new_name, goal

    @on(Button.Pressed, "#ok")
    async def action_submit(self) -> None:
        target = self._target()
        if target is None:
            return
        proj, new_name, goal = target
        run = str(self.query_one("#mode", Select).value)
        sess = {"agent": str(self.query_one("#agent", Select).value),
                "permission_mode": str(self.query_one("#perm", Select).value)}
        if run == "session":
            try:
                n = max(1, min(int(self.query_one("#count", Input).value or "1"), 12))
            except ValueError:
                n = 1
            self.dismiss({"mode": "session", "project": proj, "new_name": new_name, "goal": goal, "count": n, **sess})
            return
        if run == "lead":
            self.dismiss({"mode": "lead", "project": proj, "new_name": new_name, "goal": goal, **sess})
            return
        review = self.query_one("#review", Checkbox).value
        if not review:
            self.dismiss({"mode": "auto", "run": run, "project": proj, "new_name": new_name, "goal": goal, **sess})
            return
        if not self.proposed:
            await self._propose(proj, new_name, goal)
            return
        out = []
        for i in range(len(self.proposed)):
            try:
                if not self.query_one(f"#inc-{i}", Checkbox).value:
                    continue
                spec = self.query_one(f"#spec-{i}", TextArea).text.strip()
                acc = self.query_one(f"#acc-{i}", Input).value.strip() or None
            except Exception:
                continue
            if spec:
                out.append({"spec": spec, "acceptance": acc})
        if not out:
            self.notify("No tasks selected", severity="warning")
            return
        self.dismiss({"mode": "review", "run": run, "project": proj, "new_name": new_name, "goal": goal, "tasks": out, **sess})

    async def _propose(self, proj: str | None, new_name: str | None, goal: str) -> None:
        status = self.query_one("#status", Static)
        status.update("Planning with Claude Code… this takes 20–60 seconds")
        ok = self.query_one("#ok", Button)
        ok.disabled = True
        name = proj or new_name or ""
        repo = self.projects.get(proj or "", {}).get("repo_path", "")
        tasks, err = await _decompose.decompose(goal, name, repo, self.templates.get(proj or "", []),
                                                new_repo=proj is None)
        ok.disabled = False
        box = self.query_one("#proposals", VerticalScroll)
        await box.remove_children()
        if err:
            status.update(f"[red]{err}[/red]")
            return
        self.proposed = tasks
        self.query_one("#modal").remove_class("wide-auto")
        self.query_one("#modal").add_class("wide")
        box.display = True
        status.update(f"{len(tasks)} tasks planned. Untick any you don't want, edit inline, then ctrl+s to start them.")
        ok.label = "Start selected  ctrl+s"
        for i, t in enumerate(tasks):
            v = Vertical(
                Checkbox("include", value=True, id=f"inc-{i}"),
                TextArea(t["spec"], id=f"spec-{i}", classes="short"),
                Input(value=t.get("acceptance") or "", placeholder="acceptance (blank = soft-verify)", id=f"acc-{i}"),
                classes="proposal", id=f"p-{i}",
            )
            await box.mount(v)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class ContinueScreen(ModalScreen[dict[str, Any] | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "submit", "Continue")]

    def __init__(self, task: dict[str, Any], agents: list[str], current_agent: str | None):
        super().__init__()
        self.task = task
        self.agents = agents or ["claude_code"]
        self.current_agent = current_agent

    def compose(self) -> ComposeResult:
        others = [a for a in self.agents if a != self.current_agent] or self.agents
        with Vertical(id="modal", classes="narrow"):
            yield Label(f"Continue session {self.task['id'][:8]} on another agent", classes="title")
            yield Static("Its worktree is saved first; the new agent starts from those changes with a summary of "
                         "what happened so far. This one is closed.", classes="dim")
            yield Label("Agent")
            yield Select([(a, a) for a in others], value=others[0], id="agent", allow_blank=False)
            yield Label("Permissions")
            yield Select(PERMISSION_MODES, value="auto", id="perm", allow_blank=False)
            with Horizontal(classes="buttons"):
                yield Button("Continue  ctrl+s", variant="primary", id="ok")
                yield Button("Cancel  esc", id="cancel")

    @on(Button.Pressed, "#ok")
    def action_submit(self) -> None:
        self.dismiss({"agent": str(self.query_one("#agent", Select).value),
                      "permission_mode": str(self.query_one("#perm", Select).value)})

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "no", "No"), Binding("y", "yes", "Yes"), Binding("n", "no", "No")]

    def __init__(self, message: str, danger: bool = False):
        super().__init__()
        self.message = message
        self.danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(id="modal", classes="narrow"):
            yield Label(self.message)
            with Horizontal(classes="buttons"):
                yield Button("Yes  y", variant="error" if self.danger else "primary", id="yes")
                yield Button("No  n", id="no")

    @on(Button.Pressed, "#yes")
    def action_yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def action_no(self) -> None:
        self.dismiss(False)


HELP = """[b]Views[/b]  (tabs across the top — click them or use the keys)
  0   Queue: task table + details        i   Inbox: everything that needs you
  h   Hive: live coordination feed       [ ] previous / next tab
  A tab appears for every task or session in flight:
     ● headless task   ⌨ interactive session   ✓ done   ✗ failed   ? asks you   ! needs approval

[b]Sessions[/b]  (interactive agents in their own terminal; keys work on the Inbox, a ⌨ tab, or a ⌨ row)
  n      jump to the next thing that needs you
  y / d  approve / deny the action it is asking about
  1-9    pick an option when it asks a multiple-choice question
  t      type a message: an answer, a new instruction, or why you're denying
  enter  attach to its terminal (tmux) — prefix+d brings you back here
  F      finish: commit, push, verify, and close its terminal
  c      cancel (kills it)              r   reopen an ended session (keeps its context and files)
  o      continue on another agent (usage limit hit? its work is saved and handed over)

[b]Navigation[/b]
  j / k  ↓ / ↑     move selection, or scroll the live log
  enter            open task (log tab)     /         filter tasks (kind:session state:running …)
  1 2 3            overview / log / diff on the Queue tab
  f                follow (auto-scroll) on/off      W   auto-watch: jump to each agent as it starts

[b]Goals & tasks[/b]
  g   new goal: pick or create a project, describe it, choose how to run it, ctrl+s
        Tasks — plan it, agents work headless        Swarm — plan it, one session per subtask
        Session — one interactive session (Copies = N parallel attempts)
  a   add one exact task yourself      r   retry (edit spec first)
  m   merge a done task's branch       x   delete a task          R   refresh now

[b]Other views[/b]
  s   routing stats (agent × type × difficulty)      A   agents and liveness

  q   quit
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close"), Binding("question_mark", "close", "Close"), Binding("q", "close", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Label("Hiveswarm TUI", classes="title")
            yield Static(HELP)

    def action_close(self) -> None:
        self.dismiss(None)
