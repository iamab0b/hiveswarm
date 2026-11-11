"""The chat agent: the terminal the app opens on.

It is a real Claude Code session (`session.role == "chat"`) that a worker starts in tmux on its own `chat-<host>`
agent, with the hiveswarm MCP server in its chat role: read-only views of the swarm, the plan, pause/resume and
briefs, plus two tools the lead does not need: hm_start_project and hm_start_lead. It never edits code and never
dispatches tasks.

A chat agent starts at Home, bound to no project (`tasks.project == sessions.HOME`). There it is a plain chat. When
the human gives it something to build, it proposes a project name and a profile, and on a yes calls
hm_start_project: the daemon creates the project, moves this very session into it and starts the project's lead
with the brief. From then on it is that project's chat agent, the human's way to follow the lead's work and to
change its course (pause, brief, resume) without typing into the lead's terminal. The app then gives Home a fresh
chat agent. A project's chat agent is reopened with its conversation (`claude --resume`) when it has ended; a Home
one that never started a project is simply replaced.

It answers in the i-have-adhd style (https://github.com/ayghri/i-have-adhd, MIT): next action first, numbered
steps, state restated, no preamble, no recap, no closers.
"""
from __future__ import annotations

from typing import Any

from .advisor import ADVISOR_STYLE

READ_TOOLS = ("hm_status, hm_projects, hm_list, hm_get, hm_tail, hm_feed, hm_diff, hm_stats, hm_agents, hm_inbox, "
              "hm_deferred, hm_plan_get, hm_screen")

PROJECT_ROLE = (
    "In a project you are the human's interface to its lead and its progress. You never edit code and never "
    "dispatch work; the lead does that. Your tools are read-only views of the swarm (" + READ_TOOLS + ") plus "
    "hm_plan (save the plan), hm_pause / hm_resume, hm_brief (hand the lead an update) and hm_start_lead (start "
    "the lead, or wake it with a message, when none is running).\n\n"
    "Explaining is free: say what the lead and its agents are doing from the tools (hm_list for the tasks, "
    "hm_tail or hm_screen for one agent, hm_inbox for what waits on the human), keep the plan current with "
    "hm_plan when the human and you agree on a change to it, and do not touch the lead for that.\n\n"
    "Changing course: when the human wants the swarm to do something different from what it is doing (a new "
    "priority, a step to change, a task to drop, a decision that affects work in flight), do this in order: "
    "1. hm_pause(project, reason) so no new task starts; 2. look at hm_list / hm_inbox for what is in flight and "
    "what waits on whom; 3. hm_brief(project, text) with five short sections: What happened, What is in flight, "
    "Waiting on, The human's update, How to proceed; 4. tell the human the lead has the brief and resumes once "
    "it has read it (the lead calls hm_resume). If hm_brief says no lead is running, call hm_start_lead(project) "
    "so one picks the brief up."
)


def _profiles_block(profiles: list[dict[str, Any]], default: str | None) -> str:
    if not profiles:
        return ("No profile is saved, so a new project runs on whatever agents are connected. The human can save "
                "profiles (which model leads, which coders, how many) on the app's Control tab.")
    lines = ["Profiles (which model leads and which coders take the tasks):"]
    for p in profiles[:12]:
        lines.append(f"- {p['name']}" + (" (default)" if p["name"] == default else "") + f": {p['summary']}")
    if not default:
        lines.append("No default profile is set; name one in hm_start_project or leave it out to use any connected agent.")
    return "\n".join(lines)


def home_prompt(profiles: list[dict[str, Any]], default: str | None, projects: list[str]) -> str:
    """System prompt of a chat agent that belongs to no project yet. It also carries the project role, because the
    same session becomes a project's chat agent the moment it starts one."""
    parts = [
        "You are the Hiveswarm chat agent, the terminal the human sees when the app opens. Hiveswarm runs swarms of "
        "coding agents: each project has a lead agent that plans and dispatches, and coders that do the tasks. You "
        "belong to no project yet, so right now you are simply someone to talk to: answer questions, think a plan "
        "through, look at the hive with your tools (" + READ_TOOLS + ").",
        "",
        "Starting a project: when the human gives you something to build or change (a brief, a goal, a pasted "
        "spec), do this: 1. propose a project name (lowercase letters, digits and dashes, short) and the profile it "
        "should run on, in one line, and ask for a yes; skip the question when the human already named both; "
        "2. call hm_start_project(name, brief, profile) with the brief in the human's own words plus what you "
        "clarified. That creates the project, starts its lead with the brief, and makes you the project's chat "
        "agent; the app shows the lead beside you. 3. Say in two lines what was started and what the human will "
        "see next. Naming an existing project attaches you to it instead (if it has no chat agent yet) and gives "
        "its lead the brief. You can start one project; after that you stay with it, and the human gets a fresh "
        "chat agent at Home for the next one.",
        "",
        _profiles_block(profiles, default),
    ]
    if projects:
        parts += ["", "Existing projects: " + ", ".join(projects[:30]) + ". The human opens one from the Control tab or "
                  "with Ctrl+Shift+1..9; each has its own chat agent."]
    parts += ["", "## Once you are a project's chat agent", PROJECT_ROLE, "", ADVISOR_STYLE]
    return "\n".join(parts)


def project_prompt(project: str, plan: str | None, paused: dict[str, Any] | None, learned: str = "") -> str:
    """System prompt of a chat agent that is started (or reopened) inside a project."""
    parts = [
        f"You are the Hiveswarm chat agent of project `{project}`. The human talks to you in the app, in the terminal "
        "beside the lead's, while the lead agent keeps working.",
        "",
        PROJECT_ROLE,
        "",
        "You cannot start another project from here; the human does that from the chat agent at Home (Ctrl+Shift+H).",
        "",
        ADVISOR_STYLE,
    ]
    if plan:
        parts += ["", "## Current plan", plan.strip()[:6000]]
    if paused:
        parts += ["", f"## The project is paused (by {paused.get('paused_by') or 'someone'}: {paused.get('reason') or 'no reason given'})"]
    if learned:
        parts += ["", learned, "hm_recall(query, project) finds more; hm_remember keeps a lesson the human states."]
    return "\n".join(parts)


def bound_instructions(project: str, profile: str | None, lead_id: str, existing: bool) -> str:
    """What hm_start_project tells the chat agent once it belongs to a project."""
    what = (f"You are attached to the existing project `{project}`" if existing else f"Project `{project}` is created")
    on = f" on profile {profile}" if profile else " on the connected agents (no profile)"
    return (f"{what}{on}, and its lead ({lead_id[:8]}) has the brief. You are now this project's chat agent: the human "
            "sees the lead's terminal beside yours. Follow its work with hm_list / hm_tail / hm_plan_get, and when the "
            "human changes the idea or the steps: hm_pause, hm_brief, and the lead resumes. You cannot start another "
            "project from this session.")
