"""Regenerate docs/screenshots against the fake swarm (no model calls, no sign-ins).

    .venv/bin/python tests/screenshots.py            # writes docs/screenshots/*.png

Needs tmux, Playwright with Chromium (`playwright install chromium`, or HIVESWARM_E2E_CHROMIUM=/path/to/chrome),
and `hiveswarm-mcp` on PATH (it is, inside the project's virtualenv) for the lead session.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import Stack, free_port  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"


def attention(stack: Stack, tid: str) -> str | None:
    return (stack.session(tid).get("attention") or {}).get("kind")


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="hs-shots-"))
    stack = Stack(home)
    venv_bin = Path(sys.executable).parent
    stack.env["PATH"] = f"{venv_bin}{os.pathsep}{stack.env['PATH']}"
    stack.env["FAKE_STEP"] = "0.5"
    wt = home / "worker.toml"
    wt.write_text(wt.read_text().replace("concurrency = 2", "concurrency = 4"))
    stack.start()
    ui_port = free_port()
    ui_log = open(home / "ui.log", "ab")
    ui = subprocess.Popen([sys.executable, "-c", f"from hiveswarm.ui_server import main; main(port={ui_port}, open_browser=False)"],
                          env=stack.env, stdout=ui_log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{ui_port}"
    try:
        # ── history for the Stats page: a handful of finished tasks on both agents ──
        done = []
        for i, spec in enumerate(["Add a docstring to add()", "Add type hints to add.py", "Add subtract() with a test",
                                  "Write a module docstring", "Add __all__ to add.py", "Add a multiply() with a test"]):
            r = stack.post("/tasks", {"project": "demo", "spec": spec, "acceptance": "python3 -c 'import add'",
                                      "origin": "you", "agent": ["claude_code", "codex"][i % 2]})
            done.append(r["id"])
        for tid in done:
            stack.wait_for(lambda: stack.state(tid) in ("done", "failed"), 120, "history task")
        failing = stack.post("/tasks", {"project": "demo", "spec": "Make the tests fail on purpose", "acceptance": "false", "origin": "you"})["id"]
        stack.wait_for(lambda: stack.state(failing) == "failed", 200, "the failing task")

        # ── a standing order on the project ──
        stack.post("/directives", {"project": "demo", "text": "Never copy code from a repository we do not have permission to copy from.",
                                   "every_tools": 8, "every_minutes": 10, "check": True, "created_by": "you"})

        # ── three interactive sessions in three states: needs approval, asks a question, waiting for input ──
        a = stack.post("/sessions", {"project": "demo", "spec": "Add input validation to the signup form and cover it with tests",
                                     "permission_mode": "auto", "origin": "you"})["id"]
        b = stack.post("/sessions", {"project": "demo", "spec": "Refactor add.py into a small arithmetic module with a CLI",
                                     "permission_mode": "auto", "origin": "you"})["id"]
        c = stack.post("/sessions", {"project": "demo", "spec": "Ship the arithmetic library: fix add(), add subtract(), tests for both, then a README",
                                     "permission_mode": "auto", "agent": "codex", "origin": "you"})["id"]
        stack.wait_for(lambda: attention(stack, a) == "permission", 60, "session A permission")
        stack.wait_for(lambda: attention(stack, b) == "permission", 60, "session B permission")
        stack.post(f"/sessions/{b}/answer", {"choice": "deny"})
        stack.wait_for(lambda: attention(stack, b) == "question", 60, "session B question")
        stack.wait_for(lambda: attention(stack, c) == "input", 60, "session C idle")
        queued = stack.post("/tasks", {"project": "demo", "spec": "Write a CHANGELOG.md summarizing the arithmetic module changes",
                                       "acceptance": "test -f CHANGELOG.md", "origin": "you", "agent": "codex"})["id"]

        # ── a lead session that plans and dispatches through the MCP server ──
        lead = stack.post("/sessions", {"project": "demo", "spec": "Add a divide() that raises on zero, with tests",
                                        "permission_mode": "auto", "lead": True, "persistent": True, "origin": "you"})["id"]
        stack.wait_for(lambda: attention(stack, lead) == "question", 90, "the lead's plan question")

        with sync_playwright() as play:
            exe = os.environ.get("HIVESWARM_E2E_CHROMIUM")
            browser = play.chromium.launch(executable_path=exe) if exe else play.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1600, "height": 960}, device_scale_factor=1.25, color_scheme="dark")
            pg = ctx.new_page()
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)))

            def shot(path: str, name: str, wait: int = 2500) -> None:
                pg.goto(base + path, wait_until="load")
                pg.wait_for_timeout(wait)
                pg.screenshot(path=str(OUT / name))
                print("wrote", name)

            shot("/", "01-swarm.png", 3000)
            pg.click("text=wall")
            pg.wait_for_timeout(3000)
            pg.screenshot(path=str(OUT / "02-swarm-wall.png"))
            print("wrote 02-swarm-wall.png")
            pg.click("text=cards")
            shot(f"/sessions/{a}", "03-session-permission.png", 3000)
            shot("/inbox", "05-inbox.png")
            shot("/hive", "06-hive.png")
            shot("/", "09-new-goal.png", 1000)
            pg.keyboard.press("g")
            pg.wait_for_timeout(600)
            pg.fill("[role=dialog] textarea", "Add rate limiting to the API: a token bucket per user, config in settings.toml, tests for the limits")
            pg.wait_for_timeout(400)
            pg.screenshot(path=str(OUT / "09-new-goal.png"))
            pg.keyboard.press("Escape")
            shot("/stats", "30-stats-performance.png")
            shot(f"/lead/demo", "20-lead-chat.png", 3000)
            shot(f"/sessions/{b}", "24-session-standing-orders.png", 2500)
            try:
                pg.click("button:has-text(\"Standing orders\")", timeout=3000)
                pg.wait_for_timeout(800)
                pg.screenshot(path=str(OUT / "24-session-standing-orders.png"))
            except Exception as e:
                print("standing orders panel:", e)
            pg.evaluate("localStorage.setItem('hm.theme','light')")
            shot("/", "11-swarm-light.png", 2500)
            pg.evaluate("localStorage.setItem('hm.theme','dark')")
            browser.close()
            if errors:
                print("page errors:", errors)
        # answer what is pending so the fakes exit cleanly
        for tid in (a, b, c, lead, queued):
            try:
                stack.post(f"/tasks/{tid}/cancel")
            except Exception:
                pass
        time.sleep(1)
        return 0
    except Exception:
        for tid in stack.get("/tasks", limit=10):
            if tid.get("kind") == "session":
                print(f"--- {tid['id']} ({tid['state']}):\n" + stack.log_text(tid["id"])[-1500:])
        raise
    finally:
        ui.terminate()
        stack.stop()
        if os.environ.get("KEEP"):
            print("kept", home)
        else:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
