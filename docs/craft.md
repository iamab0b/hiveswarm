# Hiveswarm Craft

Every coding agent in a swarm works under a ruleset: a block of instructions Hiveswarm puts into every headless prompt and every interactive session, and a handoff the agent ends with that Hiveswarm reads back. The default ruleset is **Craft**: minimal code, real tests, a clear handoff. It is Hiveswarm's own take on [Ponytail](https://github.com/DietrichGebert/ponytail) (MIT), whose decision ladder it keeps; the testing and handoff rules, the ledger and the `untested` flag are Hiveswarm's.

## The rules

What the agent sees, word for word (`hiveswarm.rulesets.CRAFT_RULES`):

Minimal code, real tests, a clear handoff. These rules sit above the task; follow them even when the task is
in a hurry.

### 1. Understand first
- Read the files you will touch and the tests that cover them before you write anything. Find how this codebase
  already does this kind of thing and do it that way.
- Restate the task to yourself in one line: what changes, what must not. If the task leaves a real decision open,
  ask (in a session) or take the smallest reading that satisfies the acceptance command and say what you assumed.
- Stay inside the task. No drive-by refactors, renames, reformatting or fixes to things you noticed; list them
  under Deferred instead.

### 2. Production code: the ladder
Before writing a line, go down this list and stop at the first yes.
1. Does it need to exist at all? If the task works without it, skip it.
2. Does the codebase already have it? Reuse it.
3. Does the standard library do it? Use that.
4. Does the platform or framework do it natively? Use that.
5. Does an installed dependency do it? Use it; add no new dependency the task did not ask for.
6. Is it one line? Write one line.
7. Only then: the minimum that works and reads plainly.

No speculative abstractions, options, flags or config nobody asked for. No defensive code for states that cannot
happen. No comments that restate the code; a comment says why. No new file when an existing one is the natural
home. Delete code your change makes dead.

### 3. Tests: required, not excess
- Every behaviour you add or change gets a test that fails before your change and passes after it, next to the
  existing tests for that module and in their style.
- Test the contract (inputs, outputs, errors), not the implementation. One test per behaviour; no test per line,
  no mocking of the thing under test.
- Run the relevant tests and the acceptance command before you finish. Never weaken, skip or delete a test to
  make it pass, and never replace the test runner. If something cannot be tested here, say so under Tested.

### 4. Handoff
End your work with exactly these three sections, one line per item:
Changed: each file and why.
Tested: the commands you ran and what they reported, or "none" with the reason.
Deferred: shortcuts you took and things you noticed but left alone, or "none".

With `ruleset_intensity = "strict"` one more paragraph is added:

> No new dependencies at all. No new files unless the task names them. If finishing needs more than the task says,
stop and report what is missing instead of widening the scope.

Claude Code lanes also get the same text as a `hiveswarm-craft` skill in their config dir, so it is there when the agent looks for guidance mid-task.

## What Hiveswarm does with the handoff

When an attempt finishes, the daemon reads the agent's last messages for the three sections.

- **Deferred** lines become entries in the project's **deferred ledger**: `hm deferred [project]` lists them (`--all` includes resolved ones, `--resolve ID --note "..."` closes one), the lead reads them with `hm_deferred` and closes them with `hm_deferred_resolve`, and the Lead page shows them under the project's standing orders. Nothing an agent skipped is silently forgotten; the lead is told to fold the ones that matter into the next wave.
- **Tested** is checked against the diff. An attempt is **untested** when it changed code, touched no test file, and nothing ran a test suite: neither the acceptance command (a real test runner such as `pytest …` or `npm test` counts) nor the agent's Tested section. Docs-only changes are never untested. An untested attempt gets a flag that keeps the finished task in the inbox and on the Swarm page as *untested*, and **`hm_merge` / the merge button refuse it** until a reviewer either retries the task asking for the test or, having read the diff, merges with `acknowledge_untested=true` ("Merge anyway" in the app). The lead's review skill tells it to prefer the retry and to say why when it acknowledges.
- Every attempt records the ruleset it ran under and the lines it changed (insertions plus deletions). `hm stats` and the Stats page show attempts with and without the ruleset side by side: pass rate, lines changed, wall time and how many were untested, so you can see what the rules do on your own projects.

The handoff is tolerant: `## Changed`, `**Changed**:` and `Changed: a.py, b.py` all parse; items are one per line; "none" means empty. If the agent leaves no handoff at all, the attempt still completes; it just cannot claim to have tested anything.

## Configuration

```toml
[rulesets]
default = "craft"        # or "off"
intensity = "standard"   # or "strict"

[projects.myapp]
ruleset = "off"              # this project's agents get no ruleset
ruleset_intensity = "strict" # or stricter than the default
```

The daemon re-reads `config.toml` when it changes, so switching a project's ruleset takes effect on the next claim without a restart. `GET /projects/<name>/ruleset` shows what a project's agents currently get; `GET /projects` lists each project's ruleset and open deferred count.

## Why these rules

The ladder exists because the most expensive code is code that did not need to exist: every speculative option, wrapper and defensive branch is read, tested, maintained and merged around forever. Tests are required because an agent's own claim that "the tests pass" is the cheapest sentence in the world; a test file in the diff or a test runner in the acceptance command is evidence, a sentence is not. The handoff exists because a swarm has no hallway: the only way a shortcut gets back to the lead is if the agent writes it down in a place Hiveswarm reads.
