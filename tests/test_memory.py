"""Memory across tasks: local FTS and Hindsight backends, lessons retained from attempts, recalled by the lead and the advisor."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from hiveswarm import memory
from hiveswarm.memory import HindsightMemory, _fts_query, lesson_from_attempt, prompt_block


def test_fts_query_is_tolerant():
    assert _fts_query('rate limit "token" bucket (per user)') == '"rate" OR "limit" OR "token" OR "bucket" OR "per" OR "user"'
    assert _fts_query("a") == "" and _fts_query("") == ""


def test_lesson_and_prompt_block():
    task = {"project": "demo", "spec": "Add rate limiting\nmore", "acceptance": "pytest -q", "kind": "task"}
    att = {"agent": "codex", "lines_changed": 12, "wall_seconds": 40.2, "verifier_log": "x\nFAILED tests/test_a.py::t\nE   assert 1 == 2\n"}
    text, tags = lesson_from_attempt(task, att, {"task_type": "feature"}, passed=False)
    assert text.startswith('Failed on demo: "Add rate limiting" by codex (feature):') and "assert 1 == 2" in text
    assert tags == ["demo", "codex", "feature", "failed"]
    text, tags = lesson_from_attempt(task, att, {"task_type": "feature"}, passed=True)
    assert "12 lines changed, 40s" in text and "acceptance: pytest -q" in text and tags[-1] == "done"
    block = prompt_block([{"text": "Failed on demo: x", "scope": "demo"}, {"text": "global thing", "scope": "global"}])
    assert block.startswith("## What the swarm learned before") and "[demo]" in block and "- global thing\n" in block + "\n"
    assert prompt_block([]) == ""


class _Hindsight(BaseHTTPRequestHandler):
    calls: list[tuple[str, dict]] = []

    def log_message(self, *a):  # noqa: D401
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        _Hindsight.calls.append((self.path, body))
        if self.path.endswith("/recall"):
            if "hiveswarm-empty" in self.path:
                self.send_response(404); self.end_headers(); return
            out = {"results": [{"id": "m1", "text": f"recalled for {body['query']}", "type": "experience"}]}
        else:
            out = {"success": True, "items_count": 1}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")


def test_hindsight_backend_speaks_the_rest_api():
    srv = HTTPServer(("127.0.0.1", 0), _Hindsight)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        m = HindsightMemory(f"http://127.0.0.1:{srv.server_port}", token="t0k", budget="mid")
        r = m.remember("demo", "Failed on demo: x", ["demo", "failed"], by="daemon")
        assert r["ok"] and r["bank"] == "hiveswarm-demo"
        path, body = _Hindsight.calls[-1]
        assert path == "/v1/default/banks/hiveswarm-demo/memories"
        assert body["items"][0]["content"] == "Failed on demo: x" and body["items"][0]["tags"] == ["demo", "failed"] and body["async"] is False
        items = m.recall("rate limiting", ["demo", "empty", "global"], limit=5)
        assert [i["text"] for i in items] == ["recalled for rate limiting", "recalled for rate limiting"]
        assert {i["scope"] for i in items} == {"demo", "global"}, "a bank that does not exist yet (404) is skipped"
        assert _Hindsight.calls[-1][1]["budget"] == "mid"
        assert m.status()["ok"]
    finally:
        srv.shutdown()


def test_failed_attempt_becomes_a_lesson_the_lead_can_recall(stack):
    assert stack.get("/memory")["backend"] == "local"
    tid = stack.post("/tasks", {"project": "demo", "spec": "Make the rate limiter tests fail on purpose", "acceptance": "false", "origin": "test",
                                "agent": "claude_code"})["id"]
    stack.wait_for(lambda: stack.state(tid) == "failed", 200, "the task to fail")
    assert "remembered: Failed on demo" in stack.log_text(tid)
    r = stack.get("/memory/recall", q="rate limiter tests", project="demo")
    assert r["backend"] == "local" and r["items"] and r["items"][0]["text"].startswith("Failed on demo")
    assert r["items"][0]["scope"] == "demo" and "failed" in r["items"][0]["tags"]
    assert stack.post("/memory/remember", {"text": "The rate limiter lives in api/limits.py; its tests need redis", "project": "demo", "by": "you"})["ok"]
    assert stack.post("/memory/remember", {"text": "Always run ruff before handing off", "global_scope": True, "by": "you"})["ok"]
    got = [i["text"] for i in stack.get("/memory/recall", q="redis ruff limiter", project="demo")["items"]]
    assert any("redis" in t for t in got) and any("ruff" in t for t in got), "project and global scopes both recalled"
    assert not any("redis" in t for t in [i["text"] for i in stack.get("/memory/recall", q="redis", project="other")["items"]]), "project memories stay in their project"

    env = {**stack.env, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{stack.env['PATH']}", "HIVESWARM_ROLE": "advisor"}

    async def via_mcp(s):
        names = {t.name for t in (await s.list_tools()).tools}
        assert {"hm_recall", "hm_remember"} <= names
        res = await s.call_tool("hm_recall", {"query": "rate limiter", "project": "demo"})
        return json.loads("\n".join(c.text for c in res.content if getattr(c, "text", None)))
    from test_advisor import _with_mcp
    out = asyncio.run(_with_mcp(env, via_mcp))
    assert out["items"] and "rate limiter" in out["items"][0]["text"].lower()

    import subprocess
    cli = subprocess.run([sys.executable, "-m", "hiveswarm.cli", "recall", "redis", "--project", "demo"], env=env, capture_output=True, text=True)
    assert cli.returncode == 0 and "redis" in cli.stdout


def test_memory_off_by_default(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[daemon]\nport = 1\n")
    monkeypatch.setenv("HIVESWARM_CONFIG", str(cfg))
    memory._instance = None
    memory._instance_key = None
    assert memory.get().name == "none" and memory.recall("anything", "p") == []
    assert memory.remember("x", "p")["ok"] is False
