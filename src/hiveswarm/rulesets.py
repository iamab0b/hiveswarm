"""Hiveswarm Craft: the way every coding agent in a swarm is asked to work.

A ruleset is a block of instructions injected into every headless prompt and every interactive session, and a
handoff format the agent ends with (Changed / Tested / Deferred) that the daemon reads back: deferred items go
into the project's ledger, an attempt that changed code without touching a test and without a real check gets an
`untested` flag that blocks merging until someone looks. The decision ladder in section 2 is adapted from
Ponytail (https://github.com/DietrichGebert/ponytail, MIT); the testing and handoff rules are Hiveswarm's own.

Config: `[rulesets] default = "craft"|"off"`, `intensity = "standard"|"strict"`; per project
`projects.<name>.ruleset` and `projects.<name>.ruleset_intensity`.
"""
from __future__ import annotations

import json
import re
from typing import Any

CRAFT = "craft"
INTENSITIES = ("standard", "strict", "off")
SECTIONS = ("changed", "tested", "deferred")

CRAFT_RULES = """## Hiveswarm Craft: how to work here
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
Deferred: shortcuts you took and things you noticed but left alone, or "none"."""

STRICT_RULES = """
### Strict
No new dependencies at all. No new files unless the task names them. If finishing needs more than the task says,
stop and report what is missing instead of widening the scope."""


def render(name: str | None, intensity: str | None) -> str:
    """The prompt block for a ruleset, or "" when it is off."""
    if (name or "off") != CRAFT or (intensity or "standard") == "off":
        return ""
    text = CRAFT_RULES
    if intensity == "strict":
        text += STRICT_RULES
    return text


def resolve(cfg: Any, project: str | None) -> dict[str, Any]:
    """Which ruleset a project runs under: its own keys, else the `[rulesets]` defaults."""
    name = str(cfg.get("rulesets.default", CRAFT) or "off").lower()
    intensity = str(cfg.get("rulesets.intensity", "standard") or "standard").lower()
    if project:
        p = cfg.project(project)
        if p.get("ruleset") is not None:
            name = str(p["ruleset"]).lower()
        if p.get("ruleset_intensity") is not None:
            intensity = str(p["ruleset_intensity"]).lower()
    if name in ("", "none", "false", "off"):
        name = "off"
    if intensity not in INTENSITIES:
        intensity = "standard"
    if name == "off":
        intensity = "off"
    return {"name": name, "intensity": intensity, "text": render(name, intensity)}


def label(name: str | None, intensity: str | None) -> str:
    """What an attempt records: "craft", "craft/strict" or "off"."""
    if not name or name == "off" or intensity == "off":
        return "off"
    return name if intensity in (None, "standard") else f"{name}/{intensity}"


_HEADER = re.compile(r"^\s*(?:[#>*\-\s]*)(changed|tested|deferred)\s*\**\s*:?\s*(.*?)\s*$", re.IGNORECASE)
_NONE = re.compile(r"^\(?\s*(none|n/a|nothing|-)\s*\)?\.?$", re.IGNORECASE)


def parse_handoff(text: str | None) -> dict[str, Any]:
    """Pull the Changed / Tested / Deferred sections out of an agent's final message.

    Tolerates markdown headings, bold labels, "Changed:" inline lists and bullets. Items are one per line; an
    inline "Changed: a.py, b.py" becomes one item. `found` says whether any section was present at all."""
    out: dict[str, Any] = {k: [] for k in SECTIONS}
    out["found"] = False
    if not text:
        return out
    lines = text.splitlines()
    starts = [i for i, raw in enumerate(lines) if (m := _HEADER.match(raw)) and m.group(1).lower() == "changed"]
    if starts:  # the handoff begins at the last "Changed"; anything before it is conversation
        lines = lines[starts[-1]:]
    cur: str | None = None
    for raw in lines:
        m = _HEADER.match(raw)
        if m:
            cur = m.group(1).lower()
            out["found"] = True
            rest = m.group(2).strip()
            if rest and not _NONE.match(rest):
                out[cur].append(rest)
            continue
        if cur is None:
            continue
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            cur = None
            continue
        item = re.sub(r"^[-*•\d.)\s]+", "", line).strip()
        if item and not _NONE.match(item):
            out[cur].append(item[:300])
    for k in SECTIONS:
        out[k] = out[k][:20]
    return out


_DOC_EXT = (".md", ".rst", ".txt", ".adoc", ".markdown")
_TEST_HINT = re.compile(r"(^|/)(tests?|spec|specs|__tests__|testing)(/|$)|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$|(^|/)conftest\.py$", re.IGNORECASE)
_TEST_RUN = re.compile(r"\b(pytest|unittest|npm test|pnpm test|yarn test|bun test|vitest|jest|mocha|cargo test|go test|mvn test|gradle test|dotnet test|rspec|phpunit|make test|tox|nox|ctest|playwright)\b", re.IGNORECASE)


def files_in_stat(stat: str | None) -> list[str]:
    """File paths from `git diff --stat` output."""
    files = []
    for line in (stat or "").splitlines():
        if "|" not in line:
            continue
        name = line.split("|", 1)[0].strip()
        if "=>" in name:  # a rename: "src/{old => new}.py" or "old.py => new.py"
            name = re.sub(r"\{[^}]*=> ([^}]*)\}", r"\1", name)
            name = name.split("=>")[-1].strip()
        if name:
            files.append(name)
    return files


def classify_files(files: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"code": [], "tests": [], "docs": []}
    for f in files:
        low = f.lower()
        if _TEST_HINT.search(low):
            out["tests"].append(f)
        elif low.endswith(_DOC_EXT) or low.startswith("docs/") or "/docs/" in low or low.endswith("license"):
            out["docs"].append(f)
        else:
            out["code"].append(f)
    return out


def lines_changed(stat: str | None) -> int | None:
    """Insertions plus deletions from the summary line of `git diff --stat`."""
    m = re.search(r"(\d+) insertions?\(\+\)", stat or "")
    n = re.search(r"(\d+) deletions?\(-\)", stat or "")
    if not m and not n:
        return None
    return int(m.group(1) if m else 0) + int(n.group(1) if n else 0)


def untested_reason(acceptance: str | None, stat: str | None, handoff: dict[str, Any] | None) -> str | None:
    """Why an attempt counts as untested, or None when it is fine.

    Untested means: code changed, no test file changed, and nothing ran a test suite: neither the acceptance
    command (a real test runner counts as the check) nor the agent's own Tested section. Docs-only changes are
    never untested."""
    kinds = classify_files(files_in_stat(stat))
    if not kinds["code"]:
        return None
    if kinds["tests"]:
        return None
    if acceptance and _TEST_RUN.search(acceptance):
        return None
    tested = (handoff or {}).get("tested") or []
    if any(_TEST_RUN.search(t) for t in tested):
        return None
    n = len(kinds["code"])
    what = ", ".join(kinds["code"][:3]) + (f" and {n - 3} more" if n > 3 else "")
    if not acceptance:
        how = "soft verification only"
    else:
        how = "the acceptance command is not a test run"
    said = "; the agent reported no test run" if (handoff or {}).get("found") else "; the agent left no handoff"
    return f"changed {what} without touching a test ({how}{said})"


def handoff_json(h: dict[str, Any]) -> str:
    return json.dumps({k: h.get(k, []) for k in SECTIONS})


CRAFT_SKILL_MD = """---
name: hiveswarm-craft
description: The Hiveswarm Craft ruleset every coding agent in a swarm works under: understand first, the production-code ladder (skip, reuse, stdlib, platform, dependency, one line, minimum), tests required but not excess, and the Changed / Tested / Deferred handoff. Use whenever writing or reviewing code for a Hiveswarm task or session.
---

# Hiveswarm Craft

""" + CRAFT_RULES.replace("## Hiveswarm Craft: how to work here\n", "") + """

## Why the handoff matters

Hiveswarm reads the three sections back. Every Deferred line becomes an entry in the project's deferred ledger
(`hm_deferred` for the lead, the Lead page for the human) so a shortcut is never silently forgotten. An attempt
that changed code without touching a test and without a real test run in Tested (or in the acceptance command)
is flagged `untested`: it appears in the inbox and `hm_merge` refuses it until a reviewer acknowledges the risk.
Writing an honest Tested section is faster than arguing with the flag.

Adapted from Ponytail (https://github.com/DietrichGebert/ponytail, MIT) for the ladder; the rest is Hiveswarm's.
"""
