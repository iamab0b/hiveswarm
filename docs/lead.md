# The lead, the MCP server and standing orders

## The lead

`hm lead <project> "<what you want>"` (or the Lead page in the app) starts a persistent Claude Code session on a worker with two things a normal session does not have: the `hiveswarm` MCP server, and the `hiveswarm-lead` skill plus four companions installed into its Claude config directory. It runs in a read-only worktree of the project and never edits code itself: every change, even a one-line fix, goes through the swarm so it is verified and lands as a branch.

Its loop, from the skill:

1. **Orient** — `hm_status`, `hm_projects`, `hm_stats`, skim the repo.
2. **Plan** — split the goal into independent, verifiable tasks that can each run from the same base commit; sequential work becomes waves with a merge in between.
3. **Size the swarm** — `hm_advise(project, tasks)` classifies each task and, from the performance table and the free lanes, recommends headless vs interactive, an agent with a pass estimate, and how many waves the plan takes. The lead tells you in one line what the swarm will look like.
4. **Dispatch** — `hm_add_tasks` (headless, with an acceptance command that exits 0 only when the work is done) or `hm_new_session` (interactive, when judgment is needed), and a standing order where the work could drift from something paramount.
5. **Wait** — `hm_wait` until something needs attention, then `hm_inbox`: answer what the goal answers, approve low-risk actions, deny and redirect otherwise; correct or clear `directive` and `stalled` flags; leave what it is unsure about for you.
6. **Review** — `hm_diff` every finished branch against the spec, `hm_merge` good work, `hm_retry` with a sharper spec otherwise. A task flagged `untested` (code changed, no test touched or run; see [craft.md](craft.md)) is refused by `hm_merge` until the lead retries it asking for the test or acknowledges the risk after reading the diff.
7. **Report** — what was merged, what failed and why, what is left; then stop and wait for your next message.

The four companion skills (`hiveswarm-plan`, `-route`, `-review`, `-triage`) go deeper on writing specs and acceptance commands, choosing agents and handling probation and stalls, reading a diff before merging, and working the inbox. Their text is in `src/hiveswarm/lead_skills.py`.

`hm lead-skill` installs the same skills into your own Claude Code (`~/.claude/skills`), so you can lead from a normal `claude` session with the MCP server configured.

## The MCP server

`hiveswarm-mcp` speaks MCP over stdio and needs `HIVESWARM_URL` and `HIVESWARM_TOKEN` (both in `~/.hiveswarm/env`). Add it to any MCP client:

```json
{"mcpServers": {"hiveswarm": {"command": "hiveswarm-mcp"}}}
```

For Claude Code: `claude mcp add hiveswarm -- hiveswarm-mcp`. `HIVESWARM_ORIGIN` labels what that client creates.

| group | tools |
|---|---|
| state | `hm_status`, `hm_projects`, `hm_agents`, `hm_stats`, `hm_list`, `hm_get`, `hm_tail`, `hm_feed`, `hm_live`, `hm_screen` |
| projects | `hm_new_project`, `hm_delete_project` |
| tasks | `hm_advise`, `hm_add_tasks`, `hm_wait`, `hm_diff`, `hm_merge` (`acknowledge_untested` for a flagged task), `hm_retry`, `hm_cancel`, `hm_delete` |
| craft | `hm_deferred`, `hm_deferred_resolve` — the deferred ledger agents fill from their handoffs ([craft.md](craft.md)) |
| plan and advisor | `hm_plan`, `hm_plan_get`, `hm_pause`, `hm_resume`, `hm_brief`, `hm_briefs` — the plan on file, pausing a project, briefs between the advisor and the lead ([advisor.md](advisor.md)); `HIVESWARM_ROLE=advisor` limits the server to the read-only tools plus these |
| sessions | `hm_new_session`, `hm_sessions`, `hm_inbox`, `hm_send`, `hm_approve`, `hm_deny`, `hm_answer`, `hm_finish`, `hm_continue` |
| standing orders | `hm_directive`, `hm_directives`, `hm_directive_clear`, `hm_clear_flag` |

Every tool's docstring is its documentation; `hm_wait` is the one to read first — it blocks until a task finishes or something needs a human, reports `directive` and `stalled` flags once each, and returns early with `briefs` when the advisor has paused the project for a change of course.

## Standing orders

A standing order is a rule an agent must keep following while it works, attached to one lane (a task or session) or to a whole project (every current and future agent in it).

```bash
hm order set "Never copy code from a repository we do not have permission to copy from; write it yourself." --project myapp
hm order set "Touch nothing outside src/billing/" --task <session id> --every-tools 5
hm order list --project myapp
hm order clear <directive id>
```

What Hiveswarm does with it:

- **Prompt** — the order sits at the top of the agent's prompt under "Standing orders (paramount — they override everything below)".
- **Reminders** — every `--every-tools` tool calls (default 8) or `--every-minutes` minutes (default 10), whichever comes first. Claude Code lanes get the reminder mid-turn through hooks (`PostToolUse`, `UserPromptSubmit`); other agents get it typed into their terminal, only while they sit idle at their own prompt so it never corrupts a running command.
- **Audit** — unless `--no-check`, after each reminder the agent's recent activity (its log, plus the terminal screen for agents without hooks) goes to the decide service with the order; a likely violation flags the task. The flag shows as a `directive` item in the inbox and on the session, is reported by `hm_wait`, and is cleared with `hm order unflag --task <id>` / `hm_clear_flag` once you have looked.

Write orders as one or two plain, testable sentences that say what to do instead of the forbidden thing. Most tasks do not need one.
