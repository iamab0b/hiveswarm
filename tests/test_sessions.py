"""Interactive sessions in tmux: permission requests, questions, free text, finish; standing orders and their audit."""
from __future__ import annotations

import time

from conftest import needs_tmux


def _attention(stack, tid):
    return (stack.session(tid).get("attention") or {}).get("kind")


@needs_tmux
def test_session_permission_question_finish(stack):
    r = stack.post("/sessions", {"project": "demo", "spec": "Session test: improve add.py", "permission_mode": "auto", "origin": "test"})
    tid = r["id"]
    stack.wait_for(lambda: _attention(stack, tid) == "permission", 60, "the force-push permission request")
    att = stack.session(tid)["attention"]
    assert "force" in att["summary"]
    assert att.get("risk") == "high"
    assert "auto-approved" in stack.log_text(tid), "the low-risk pytest call should have been auto-approved"
    stack.post(f"/sessions/{tid}/answer", {"choice": "deny"})
    stack.wait_for(lambda: _attention(stack, tid) == "question", 60, "the design question")
    assert len(stack.session(tid)["attention"]["options"]) == 3
    stack.post(f"/sessions/{tid}/answer", {"choice": "2"})
    stack.wait_for(lambda: _attention(stack, tid) == "input", 60, "the session to wait for input")
    stack.post(f"/sessions/{tid}/send", {"text": "also say hello"})
    time.sleep(2)
    stack.wait_for(lambda: _attention(stack, tid) == "input", 60, "the turn to finish")
    assert "also say hello" in stack.log_text(tid)
    stack.post(f"/sessions/{tid}/finish")
    stack.wait_for(lambda: stack.state(tid) in ("done", "failed"), 90, "finish to commit and verify")
    assert stack.state(tid) == "done", stack.log_text(tid)[-2000:]


@needs_tmux
def test_approve_is_rejected_when_nothing_is_pending(stack):
    r = stack.post("/sessions", {"project": "demo", "spec": "Session test 2", "permission_mode": "auto", "origin": "test"})
    tid = r["id"]
    stack.wait_for(lambda: _attention(stack, tid) == "permission", 60, "permission request")
    stack.post(f"/sessions/{tid}/answer", {"choice": "deny"})
    stack.wait_for(lambda: _attention(stack, tid) == "question", 60, "question")
    resp = stack.http.post(f"/sessions/{tid}/answer", json={"choice": "approve"})
    assert resp.status_code == 400
    stack.post(f"/tasks/{tid}/cancel")


@needs_tmux
def test_standing_order_reminder_and_violation_flag(stack):
    d = stack.post("/directives", {"project": "demo", "text": "Never copy code from a repository we do not have permission to copy from.",
                                   "every_tools": 2, "every_minutes": 10, "check": True, "created_by": "test"})
    did = d["id"]
    try:
        r = stack.post("/sessions", {"project": "demo", "spec": "Directive test", "permission_mode": "auto", "origin": "test"})
        tid = r["id"]
        stack.wait_for(lambda: _attention(stack, tid) == "permission", 60, "permission")
        stack.wait_for(lambda: "## Standing orders" in stack.log_text(tid), 30, "the order at the top of the prompt (transcript tail)")
        stack.wait_for(lambda: "reminded (hook) of the standing order" in stack.log_text(tid), 30, "a hook reminder")
        stack.post(f"/sessions/{tid}/answer", {"choice": "deny"})
        stack.wait_for(lambda: _attention(stack, tid) == "question", 60, "question")
        stack.post(f"/sessions/{tid}/answer", {"choice": "1"})
        stack.wait_for(lambda: _attention(stack, tid) == "input", 60, "input")
        stack.post(f"/sessions/{tid}/send", {"text": "I copied from github.com/other/repo to save time"})
        time.sleep(3)
        stack.post(f"/sessions/{tid}/send", {"text": "carry on"})

        def flagged():
            items = stack.get("/inbox")["items"]
            return any(i["id"] == tid and i["attention"]["kind"] == "directive" for i in items)
        stack.wait_for(flagged, 60, "a directive flag in the inbox")
        assert "directive check failed" in stack.log_text(tid)
        stack.post(f"/tasks/{tid}/flags/clear", kind="directive")
        assert not flagged()
        stack.post(f"/tasks/{tid}/cancel")
    finally:
        stack.delete(f"/directives/{did}")


@needs_tmux
def test_screen_snapshot_and_inbox_ordering(stack):
    r = stack.post("/sessions", {"project": "demo", "spec": "Screen test", "permission_mode": "auto", "origin": "test"})
    tid = r["id"]
    stack.wait_for(lambda: _attention(stack, tid) == "permission", 60, "permission")
    items = stack.get("/inbox")["items"]
    assert items[0]["attention"]["kind"] in ("question", "permission"), "the most urgent item comes first"
    stack.post(f"/tasks/{tid}/cancel")
    stack.wait_for(lambda: stack.state(tid) == "abandoned", 30, "cancel")
