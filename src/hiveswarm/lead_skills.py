"""The lead's supporting skills, installed next to `hiveswarm-lead` in the lead's Claude Code config dir.

Each one covers one job the lead does badly when it improvises: planning tasks agents cannot misread, choosing
agents from the performance data, reviewing a diff against the spec and the standing orders, and handling the
inbox without bothering the human for things the goal already answers. Claude Code loads a skill when its
description matches what the lead is doing, and the main skill points at them by name.
"""
from __future__ import annotations

PLAN_MD = """---
name: hiveswarm-plan
description: Split a goal into independent, verifiable Hiveswarm tasks with specs an agent cannot misread and acceptance commands that exit 0 only when the work is right. Use when the lead is planning, re-planning after a failure, or writing a retry spec.
---

# Planning tasks for a swarm

A task is good when a stranger with no memory of this conversation can finish it alone in a fresh worktree and a
command can prove it. Everything below serves that.

## Shape

- **Independent**: no task may depend on another task's output. Each starts from the same base commit. If step B
  needs step A, put them in one task, or run A in wave 1, merge, then B in wave 2.
- **Small**: one concern, roughly 15–60 minutes of agent work. Two small tasks beat one medium task: agents are more
  reliable on narrow work, failures are cheaper, and the swarm runs them in parallel.
- **Non-overlapping**: two tasks that edit the same file will conflict at merge. Split by file or module, not by
  feature slice, or put them in different waves.
- **Typical count**: 2–8 per wave. One is fine. More than 8 means the goal is really several goals.

## The spec (what the agent receives)

Write it as instructions, not a story. Include, in this order:

1. **Where**: the files, functions, or modules to touch, by path. Say what must not be touched.
2. **What**: the behaviour, with concrete examples (inputs → outputs, the error to raise, the exact flag name).
3. **How to check**: the command the agent should run itself before finishing (usually the acceptance command).
4. **Constraints**: language/version, no new dependencies unless named, style to follow (point at an existing
   file), what to leave for a later task.

Avoid: "improve", "clean up", "make it better", "as appropriate" — an agent cannot verify those. Replace with the
observable result you want.

## Acceptance commands

- Must exit 0 only when the task is correctly done, and run from the repo root in a fresh checkout.
- Install their own dependencies: `pip install -r requirements.txt && pytest -q tests/test_x.py`,
  `npm ci && npm test -- --grep "rate limit"`, `cargo test rate_limit`.
- Test the *task*, not the whole repo, so an unrelated pre-existing failure does not sink it. Scope with a path,
  `-k`, `--grep`, or a test name.
- For work with no test yet, make writing the test part of the task and name the test in the command.
- `null` acceptance means soft verification (the diff is accepted if the agent made changes). Use it only for
  docs and other work nothing mechanical can check, and review those diffs more carefully.
- Use the project's acceptance templates (`hm_projects` shows them) when they fit.

## Retry specs

When an attempt fails, do not resend the same spec. Read `hm_tail` and the verifier output, then add to the spec:
what went wrong the first time, what to do instead, and the exact failing check. The retry goes to a different
agent by default; say so if you want the same one.
"""

ROUTE_MD = """---
name: hiveswarm-route
description: Choose which agent runs which Hiveswarm task from the performance data (pass rates, seconds per step, slow steps, probation), decide headless vs interactive, size waves to the free lanes, and handle a slow or failing agent. Use when the lead is about to dispatch, or when hm_wait reports a stalled lane.
---

# Choosing agents

`hm_advise(project, tasks)` does the first pass: the decide model classifies each task (type, difficulty, whether it needs a
human), the performance table scores the candidates, and it reports free lanes and waves. `hm_stats` has the
full table. Read both before dispatching; override the advice only with a reason you can state in one line.

## What the numbers mean

- **pass rate** per agent × task type: how often its work verified. Below 40% on 3+ attempts is *failing*.
- **per step**: average seconds one tool call takes. An agent whose steps take 2× the best agent's on the same
  kind of work is *slow*. Slow steps (over 120s) are counted separately; 30%+ slow steps is also *slow*.
- **trend**: recent attempts vs earlier ones; above 1.5 means it is getting slower.
- **status**: healthy, slow, failing, unknown (fewer than 2 attempts).

## Probation

A slow or failing agent is on probation *for that task type only*. The router already enforces this: it will not
give that agent hard work (difficulty 3+) of that type while others are available, but keeps giving it easy
work so its numbers can recover. Do the same when you pick:

- Important or hard tasks of that type: a healthy agent.
- One easy, low-risk task of that type per wave: the probation agent, on purpose, so the data refreshes. Say
  so in your report ("codex is on probation for tests; gave it the small one").
- If it fails or stalls again, stop sending it that type until the human says otherwise.
- An agent on probation for one type is still fine for other types.

## Headless vs interactive

- Headless (`hm_add_tasks`): the spec and the acceptance command determine the answer. Cheapest, parallel,
  verified automatically.
- Interactive (`hm_new_session`): design choices, taste, unclear scope, or the advice says `needs human`. The
  human can watch and steer. Use `permission_mode="auto"`.
- Rule of thumb: if you would want to look over the agent's shoulder, make it a session.

## Waves and lanes

- Free lanes come from the advice. Dispatch at most that many tasks; the rest queue and start as lanes free up.
- Merge between waves. Do not dispatch wave 2 until wave 1's branches are merged, or its base is stale.
- Prefer the agent's own strength: Claude Code for reasoning-heavy and multi-file work, Codex for refactors and
  tests, Cursor for frontend, a local model on the hub (if you run one) for small and well-specified pieces. The table overrides
  these defaults once it has data.

## Stalled lanes

`hm_wait` returns attention kind `stalled` when a lane sits on one tool call past the stall limit (default 3
minutes) or logs nothing for 6. Check `hm_live(task_id)` for the step and how long it has run, and `hm_tail`.

- A full test suite or an install can legitimately take minutes: `hm_clear_flag(task_id, "stalled")` and wait.
- A trivial command (an import, a one-file edit, a `ls`) taking minutes means the agent is stuck: `hm_cancel`,
  then `hm_retry` with the same spec — the router picks another agent, and the stall lands in the stats.
- A session: `hm_screen` shows the terminal; a stuck prompt can be answered with `hm_send` or keys.
"""

REVIEW_MD = """---
name: hiveswarm-review
description: Review a finished Hiveswarm task's diff against its spec, the acceptance command, and any standing orders, then merge, retry with a sharper spec, or escalate. Use when hm_wait reports a task done and before every hm_merge.
---

# Reviewing before merging

Never merge a branch you have not read. The verifier proves the acceptance command passed; you check that the
change is the one that was asked for, nothing more, nothing worse.

## Steps

1. `hm_diff(task_id)`: read the whole diff, not the stat line.
2. Compare with the spec, point by point: every requested change present, nothing outside the named files or
   scope, no behaviour the spec did not ask for.
3. Check for the usual agent shortcuts:
   - tests weakened, skipped, or replaced with stubs so the acceptance command passes
   - the acceptance command itself edited, or a wrapper script added around the real test runner
   - hard-coded values where the spec asked for a general solution
   - copied code where a standing order forbade it, or new dependencies where none were allowed
   - unrelated reformatting that will make later merges conflict
4. Standing orders (`hm_directives`): confirm the diff honours each one that applied to this lane.
5. `hm_tail(task_id)` when the diff looks odd: what the agent said it did, and what the verifier printed.

## Decide

- **Merge** (`hm_merge`) when it does what was asked and nothing else. Merge in an order that avoids conflicts
  (small, independent branches first).
- **Retry** (`hm_retry` with a new spec) when it is close but wrong: say exactly what is wrong and what to do
  instead; keep the acceptance command unless it was the problem.
- **Escalate** to the human when the work is correct but reveals a decision the goal did not make (two valid
  designs, a scope question, a risky migration). Say what you found and what you recommend, then wait.
- **Soft-verified work** (null acceptance) gets the strictest read; nothing mechanical checked it.

## After the goal

Write what the swarm learned into the project's wiki when it has one (`.wiki/learnings.md` in a headless task
or session): which agent did well on what, environment quirks (missing tools, slow installs), and specs that
had to be sharpened. The next lead reads it.
"""

TRIAGE_MD = """---
name: hiveswarm-triage
description: Handle the Hiveswarm inbox on the human's behalf — approve or deny agent actions by risk, answer agents' questions from the goal, act on standing-order and stalled flags, and escalate only what the goal does not settle. Use when hm_wait returns needs_attention.
---

# Triage

`hm_inbox` lists everything waiting: permissions, questions, sessions waiting for input, `directive` flags,
`stalled` lanes, usage limits, failures. Work through it most urgent first; do not leave the loop blocked on
something you can settle.

## Permissions (an agent wants to run something)

Decide by what the action can destroy or expose, not by how it is phrased.

- **Approve** (`hm_approve`): reading files, running the project's tests or build, installing the project's own
  declared dependencies, editing files inside the worktree, git operations that stay on the task's branch.
- **Deny with a message** (`hm_deny(id, message)`): anything that leaves the worktree or the branch — force
  pushes, pushing to main, deleting branches, `rm -rf` outside the worktree, writing to other directories,
  curl-piping installers, changing global config, network calls to unfamiliar hosts, anything touching
  credentials. Tell the agent what to do instead.
- **Leave for the human** (say so in your reply, keep waiting): actions with real consequences you cannot
  judge — deploys, migrations against shared data, paying for something, sending messages, anything the goal
  did not mention and you would not do yourself without asking.

## Questions (an agent asks how to proceed)

- If the goal, the spec, or the repo answers it, answer (`hm_answer` with the option number or text). Prefer the
  option that keeps scope small and matches existing code.
- If it is a real product or architecture choice the goal left open, do not guess: ask the human
  (AskUserQuestion) with numbered options and your recommendation, and answer the agent once the human replies.

## Sessions waiting for input

A session that finished its turn is waiting for the next instruction. If its work is done, `hm_finish` (its
worktree is committed, pushed and verified). If not, `hm_send` the next step.

## Standing-order flags (`directive`)

The decide model thinks a lane may be breaking a standing order. Read `hm_tail`; then `hm_send` a correction (what to undo,
what to do instead), or `hm_cancel` and `hm_retry` with the order written into the spec, or
`hm_clear_flag(id, "directive")` for a false alarm. Never merge a flagged lane's work without reading the diff
against the order.

## Stalled lanes

Follow the stalled section of `hiveswarm-route`: long test suites are fine (clear the flag), trivial commands
taking minutes are not (cancel and retry elsewhere).

## Usage limits and failures

- Usage limit on a Claude Code session: `hm_continue(id, agent)` hands its work and context to another agent.
- Failed task: read the verifier output in `hm_tail`; retry with a sharper spec if the fix is clear, otherwise
  report it to the human with the reason.

## Reporting

After handling the inbox, one line per decision in your reply: what asked, what you did, why. The human sees
the same items in the app and should be able to follow your reasoning at a glance.
"""

SKILLS: dict[str, str] = {
    "hiveswarm-plan": PLAN_MD,
    "hiveswarm-route": ROUTE_MD,
    "hiveswarm-review": REVIEW_MD,
    "hiveswarm-triage": TRIAGE_MD,
}
