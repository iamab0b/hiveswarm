# The chat agent

The terminal the app opens on. It is a real Claude Code session, so everything you know about Claude Code's own interface applies, with the `hiveswarm` MCP server in its **chat role**: it reads the swarm, keeps the plan, pauses a project and briefs its lead, and starts projects. It never edits code and never dispatches a task; the lead does that.

## At Home: just a chat

A chat agent starts at Home, bound to no project. Ask it anything, think a plan through with it, or have it look at the hive (`hm_status`, `hm_list`, `hm_stats` and the other read-only tools). It can read files and search the web; `Bash`, `Edit` and `Write` are removed from its tools.

If you only chat, nothing is kept: the next time the app opens, Home has a fresh chat agent and the old one is closed and deleted. A reload of the same window keeps the one you have. The small circular arrow in the tile's title line starts a fresh one on demand.

## Starting a project

Paste a brief, or say what you want built. The chat agent proposes a project name and a profile in one line and asks for a yes (it skips the question when you named both), then calls `hm_start_project(name, brief, profile)`:

1. the hub creates the project (an empty git repository under `paths.projects_root`) and gives it the profile, or the [default profile](profiles.md#the-default-profile) when none is named;
2. that same chat session, with its conversation, moves into the project and becomes the project's chat agent;
3. the project's lead starts with your brief.

The window follows: the terminal you were typing in stays where it is, the lead's terminal splits in beside it, and the address becomes the project's workspace. Home then gets a fresh chat agent of its own, behind <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>H</kbd>.

Naming a project that already exists attaches the chat agent to it instead, if it has no chat agent yet, and hands its lead the brief. A chat agent starts one project; after that it stays with it.

## In a project: your interface to the lead

Beside the lead, the chat agent is how you follow the work and change its course without typing into the lead's terminal:

- **Following.** Ask what is going on. It answers from the tools (`hm_list` for the tasks, `hm_tail` or `hm_screen` for one agent, `hm_inbox` for what waits on you, `hm_plan_get` for the plan) and keeps the plan current with `hm_plan` when you and it agree on a change to it. None of this touches the lead.
- **Changing course.** When the idea or the steps change, say so. It pauses the project (`hm_pause`: no new task starts, nothing merges; agents in flight finish), looks at what is in flight, and hands the lead a brief (`hm_brief`) in five short sections: what happened, what is in flight, what waits on whom, your update, how to proceed. The lead gets it in its terminal when it sits at its prompt, or from `hm_wait` when it is working; it adjusts, says in two lines what changed, and resumes the project. The workspace shows a *paused* line with a *Resume now* button for the case where you do not want to wait.
- **No lead running.** `hm_start_lead` starts it, or reopens it with its conversation; it reads the briefs that were waiting.

It answers in the i-have-adhd style ([ayghri/i-have-adhd](https://github.com/ayghri/i-have-adhd), MIT): next action first, numbered steps, the state restated in one line, no preamble and no recap.

A project's chat agent persists. Close its tile, or exit Claude Code in it, and the workspace offers *Reopen the chat agent*; it also reopens by itself the next time you open that workspace. Either way it comes back with its conversation (`claude --resume`). Opening a project's workspace that has no chat agent yet (a project made from the Control tab or with `hm`) starts one inside the project.

## The model

One setting for every chat agent, on the Control tab under Profiles: model and effort for Claude Code. It is not part of a profile because a chat agent exists before any project does. It applies to chat agents opened from then on. In `config.toml` on the hub:

```toml
[chat]
model = "sonnet"
effort = "low"
```

## Where it runs

Each worker that has tmux and serves Claude Code (an enabled `[agents.*]` table on the `claude_code` adapter) registers one more agent by itself, `chat-<host>`, with one lane per chat agent that is open plus a spare, so the next one starts at once. The hub keeps the count; it is not a lane for work and the router never gives it a task. A chat agent that sits at its prompt makes no requests, so the ones you leave open cost nothing until you type. To turn it off on a machine, in its `worker.toml`:

```toml
[chat]
enabled = false
```

The same table can carry settings for Claude Code on that machine (`config_dir`, for example); the chat agent otherwise borrows them from `[agents.claude_code]`.

Its terminal is real only on the machine the app runs on. From another machine a chat tile shows the agent's output as a stream.

## Tools of the chat role

Read-only views: `hm_status`, `hm_projects`, `hm_agents`, `hm_stats`, `hm_live`, `hm_list`, `hm_get`, `hm_tail`, `hm_feed`, `hm_diff`, `hm_deferred`, `hm_sessions`, `hm_inbox`, `hm_directives`, `hm_screen`, `hm_profiles`, `hm_plan_get`, `hm_recall`. Steering: `hm_plan`, `hm_pause`, `hm_resume`, `hm_brief`, `hm_remember`. Its own: `hm_start_project`, `hm_start_lead`.

To give your own Claude Code the same role outside the app:

```bash
claude mcp add hiveswarm -e HIVESWARM_URL=http://hub:7778 -e HIVESWARM_TOKEN=… -e HIVESWARM_ROLE=chat -- hiveswarm-mcp
```

(`hm_start_project` needs a session the app started; the rest works anywhere.)

## From 0.3

The advisor, a chat panel on the Lead page that ran headless turns and kept its conversation on the hub, is gone; the chat agent does what it did, in a terminal. Plans, pauses and briefs are unchanged. `HIVESWARM_ROLE=advisor` still works and means the chat role without the two project tools.
