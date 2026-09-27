"""Connection log: per-request records, outage detection, the `hm net` summary, and the long-polled claim."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import httpx

from hiveswarm import netlog

ROOT = Path(__file__).resolve().parent.parent


def test_netlog_records_requests_errors_and_outages(tmp_path, caplog):
    nl = netlog.NetLog("worker", path=tmp_path / "net-worker.jsonl", max_bytes=10_000_000)
    # a client against a port nobody listens on: every request is a connection error
    dead = netlog.LoggedClient(nl, base_url="http://127.0.0.1:9", timeout=0.5)
    with caplog.at_level(logging.WARNING, logger="hiveswarm.net"):
        for _ in range(4):
            try:
                dead.post("/claim", json={"agent_id": "x"})
            except httpx.HTTPError:
                pass
        assert any("daemon unreachable" in r.message for r in caplog.records)
        nl.record("POST", "/claim", 200, 12.0, sent=20, received=4)
        assert any("reachable again" in r.message for r in caplog.records)
    events = [json.loads(line) for line in (tmp_path / "net-worker.jsonl").read_text().splitlines()]
    kinds = [("req" if "m" in e else ("start" if e.get("outage_start") else "end")) for e in events]
    assert kinds.count("req") == 5 and "start" in kinds and "end" in kinds
    assert all(e.get("err", "").startswith("ConnectError") for e in events if "m" in e and e.get("s") is None)
    s = netlog.summarize(events, 600)
    assert s["requests"] == 5 and s["errors"] == 4
    assert s["endpoints"][0]["path"] == "/claim" and s["endpoints"][0]["errors"] == 4
    assert len(s["outages"]) == 1 and s["outages"][0]["fails"] == 4
    text = netlog.render(s)
    assert "outages:" in text and "ConnectError" in text and "/claim" in text


def test_netlog_buckets_ids_and_rotates(tmp_path):
    nl = netlog.NetLog("ui", path=tmp_path / "net-ui.jsonl", max_bytes=400)
    for i in range(30):
        nl.record("GET", f"/tasks/{'a1b2c3d4e5f6a7b8'}/log", 200, 1.0)
    assert (tmp_path / "net-ui.jsonl.1").exists(), "the file rotates past max_bytes"
    events = netlog.read_events(["ui"], logs_dir=tmp_path)
    assert events and all(e["p"] == "/tasks/{id}/log" for e in events)


def test_stack_logs_long_polled_claims(stack):
    """The running worker records its claims: with long-polling, an idle lane makes a request every ~20 s, not
    every poll_seconds, and the log shows that."""
    path = stack.home / "logs" / "net-worker.jsonl"
    def claims_logged() -> list[dict]:
        if not path.exists():
            return []
        events = [json.loads(line) for line in path.read_text().splitlines()]
        return [e for e in events if e.get("p") == "/claim"]
    claims = stack.wait_for(claims_logged, 45, "a long-polled claim to be answered and logged")
    assert all(e["s"] == 200 for e in claims)
    assert any(e["ms"] >= 5000 for e in claims), "idle claims are held by the daemon (long-poll), not answered at once"
    env = {**stack.env, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{stack.env['PATH']}"}
    out = subprocess.run([sys.executable, "-m", "hiveswarm.cli", "net", "--since", "30m", "--json"], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    summary = json.loads(out.stdout)
    assert any(e["path"] == "/claim" and e["role"] == "worker" for e in summary["endpoints"])
