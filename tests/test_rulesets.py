"""Hiveswarm Craft: the ruleset reaches every agent's prompt, the handoff is read back into the deferred ledger,
and an attempt that changed code without a test is flagged untested and refused by merge until acknowledged."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hiveswarm import rulesets
from hiveswarm.config import Config

ROOT = Path(__file__).resolve().parent.parent

STAT_CODE = " src/app.py | 12 ++++++------\n 1 file changed, 6 insertions(+), 6 deletions(-)\n"
STAT_WITH_TEST = " src/app.py | 4 ++\n tests/test_app.py | 9 +++++++++\n 2 files changed, 13 insertions(+)\n"
STAT_DOCS = " README.md | 3 ++-\n docs/config.md | 2 +-\n 2 files changed, 3 insertions(+), 2 deletions(-)\n"


def test_render_and_resolve():
    assert "the ladder" in rulesets.render("craft", "standard")
    assert "Strict" in rulesets.render("craft", "strict")
    assert rulesets.render("craft", "off") == "" and rulesets.render("off", "standard") == ""
    cfg = Config({"rulesets": {"default": "craft", "intensity": "standard"},
                  "projects": {"loose": {"ruleset": "off"}, "tight": {"ruleset_intensity": "strict"}, "plain": {}}})
    assert rulesets.resolve(cfg, "plain")["intensity"] == "standard"
    assert rulesets.resolve(cfg, "loose") == {"name": "off", "intensity": "off", "text": ""}
    assert rulesets.resolve(cfg, "tight")["intensity"] == "strict"
    assert rulesets.label("craft", "standard") == "craft" and rulesets.label("craft", "strict") == "craft/strict"
    assert rulesets.label("off", "off") == "off"
    assert rulesets.resolve(Config({}), "anything")["name"] == "craft", "on by default"


def test_parse_handoff_shapes():
    h = rulesets.parse_handoff("Some chat first.\n\n## Changed\n- a.py: new helper\n- b.py\n\n**Tested**: pytest -q (3 passed)\n\nDeferred:\n1. rename x\n2. none of this")
    assert h["found"] and h["changed"] == ["a.py: new helper", "b.py"]
    assert h["tested"] == ["pytest -q (3 passed)"]
    assert h["deferred"] == ["rename x", "none of this"]
    h = rulesets.parse_handoff("Changed: a.py\nTested: none\nDeferred: none")
    assert h["changed"] == ["a.py"] and h["tested"] == [] and h["deferred"] == [] and h["found"]
    assert not rulesets.parse_handoff("I did the thing.")["found"]
    assert not rulesets.parse_handoff(None)["found"]
    two = rulesets.parse_handoff("Changed: old.py\nTested: pytest\n\nlater...\n\nChanged: new.py\nTested: none\nDeferred: docs")
    assert two["changed"] == ["new.py"] and two["deferred"] == ["docs"], "the last handoff wins"


def test_file_classes_and_lines():
    assert rulesets.classify_files(rulesets.files_in_stat(STAT_WITH_TEST)) == {"code": ["src/app.py"], "tests": ["tests/test_app.py"], "docs": []}
    assert rulesets.classify_files(["a/b/foo_test.go", "x.spec.ts", "lib/__tests__/y.js", "conftest.py", "docs/a.md", "main.rs"]) == {
        "code": ["main.rs"], "tests": ["a/b/foo_test.go", "x.spec.ts", "lib/__tests__/y.js", "conftest.py"], "docs": ["docs/a.md"]}
    assert rulesets.lines_changed(STAT_CODE) == 12 and rulesets.lines_changed(STAT_WITH_TEST) == 13
    assert rulesets.lines_changed("") is None
    assert rulesets.files_in_stat(" src/{old => new}.py | 2 +-\n") == ["src/new.py"]


def test_untested_reason():
    none = {"changed": [], "tested": [], "deferred": [], "found": False}
    said = {"changed": ["src/app.py"], "tested": ["python -m pytest -q: 4 passed"], "deferred": [], "found": True}
    assert rulesets.untested_reason(None, STAT_DOCS, none) is None, "docs are never untested"
    assert rulesets.untested_reason(None, STAT_WITH_TEST, none) is None, "a test file changed"
    assert rulesets.untested_reason("pytest -q tests/test_app.py", STAT_CODE, none) is None, "the acceptance command is a test run"
    assert rulesets.untested_reason(None, STAT_CODE, said) is None, "the agent ran the tests"
    r = rulesets.untested_reason(None, STAT_CODE, none)
    assert r and "src/app.py" in r and "soft verification" in r and "no handoff" in r
    r = rulesets.untested_reason("python3 -c 'import app'", STAT_CODE, {"changed": ["src/app.py"], "tested": ["none"], "deferred": [], "found": True})
    assert r and "not a test run" in r and "reported no test run" in r


def _attempt(stack, tid: str) -> dict:
    return [a for a in stack.task(tid)["attempts"] if a["outcome"]][-1]


def _flags(stack, tid: str) -> list[dict]:
    import json
    raw = stack.task(tid)["task"].get("flags")
    return json.loads(raw) if isinstance(raw, str) else list(raw or [])


def test_ruleset_reaches_prompts_and_reads_the_handoff_back(stack):
    """Both agents get the Craft block, their Deferred lines land in the ledger, and a tested change is not flagged."""
    r = stack.get("/projects/demo/ruleset")
    assert r["name"] == "craft" and "Changed:" in r["text"]
    assert stack.get("/projects")["demo"]["ruleset"] == "craft"
    tid = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add() [defer: split add.py into a package]",
                                "acceptance": "python3 -c 'import add'", "origin": "test", "agent": "claude_code"})["id"]
    stack.wait_for(lambda: stack.state(tid) == "done", 120, "the claude task to finish")
    att = _attempt(stack, tid)
    assert att["ruleset"] == "craft" and att["untested"] == 0 and att["lines_changed"] and att["lines_changed"] > 0
    ledger = stack.get("/projects/demo/deferred")
    mine = [i for i in ledger["items"] if i["task_id"] == tid]
    assert mine and mine[0]["text"] == "split add.py into a package" and mine[0]["agent"] == "claude_code"
    assert stack.get("/projects")["demo"]["deferred_open"] >= 1
    assert "untested" not in {f["kind"] for f in _flags(stack, tid)}
    done = stack.post(f"/deferred/{mine[0]['id']}/resolve", {"by": "test", "note": "not worth it"})
    assert done["ok"] and done["item"]["resolved_by"] == "test"
    assert not [i for i in stack.get("/projects/demo/deferred")["items"] if i["id"] == mine[0]["id"]]
    assert [i for i in stack.get("/projects/demo/deferred", include_resolved="true")["items"] if i["id"] == mine[0]["id"]]
    env = {**stack.env, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{stack.env['PATH']}"}
    out = subprocess.run([sys.executable, "-m", "hiveswarm.cli", "deferred", "demo", "--all"], env=env, capture_output=True, text=True)
    assert out.returncode == 0 and "split add.py" in out.stdout


def test_untested_change_is_flagged_and_merge_needs_an_acknowledgement(stack):
    tid = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add() [skip tests]",
                                "acceptance": "python3 -c 'import add'", "origin": "test", "agent": "claude_code"})["id"]
    stack.wait_for(lambda: stack.state(tid) == "done", 120, "the task to finish")
    att = _attempt(stack, tid)
    assert att["untested"] == 1
    flags = _flags(stack, tid)
    assert flags and flags[-1]["kind"] == "untested" and "add.py" in flags[-1]["summary"]
    inbox = stack.get("/inbox")["items"]
    item = next((i for i in inbox if i["id"] == tid), None)
    assert item and item["attention"]["kind"] == "untested", "a done-but-untested task waits in the inbox"
    refused = stack.post(f"/tasks/{tid}/merge")
    assert not refused["ok"] and refused.get("untested") and "acknowledge_untested" in refused["reason"]
    merged = stack.post(f"/tasks/{tid}/merge", acknowledge_untested="true")
    assert merged["ok"], merged
    assert not [f for f in _flags(stack, tid) if f["kind"] == "untested"]
    assert not any(i["id"] == tid for i in stack.get("/inbox")["items"])
    rows = stack.get("/stats/rulesets")["rows"]
    craft = next(r for r in rows if r["ruleset"] == "craft")
    assert craft["n"] >= 2 and craft["untested"] >= 1 and craft["avg_lines"] is not None


def test_project_can_switch_the_ruleset_off(stack):
    cfg_path = Path(stack.env["HIVESWARM_CONFIG"])
    original = cfg_path.read_text()
    cfg_path.write_text(original + '\n[projects.demo]\nruleset = "off"\n' if "[projects.demo]" not in original
                        else original.replace("[projects.demo]\n", '[projects.demo]\nruleset = "off"\n', 1))
    try:
        stack.wait_for(lambda: stack.get("/projects/demo/ruleset")["name"] == "off", 20, "the daemon to reload the config")
        tid = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add() [skip tests]",
                                    "acceptance": "python3 -c 'import add'", "origin": "test", "agent": "claude_code"})["id"]
        stack.wait_for(lambda: stack.state(tid) == "done", 120, "the task to finish")
        att = _attempt(stack, tid)
        assert att["ruleset"] == "off" and att["untested"] == 0, "no ruleset, no flag"
        assert stack.post(f"/tasks/{tid}/merge")["ok"]
    finally:
        cfg_path.write_text(original)
        stack.wait_for(lambda: stack.get("/projects/demo/ruleset")["name"] == "craft", 20, "the config to be restored")
