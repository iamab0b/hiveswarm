"""Regenerate docs/screenshots against the fake swarm (no model calls, no sign-ins).

    .venv/bin/python tests/screenshots.py            # writes docs/screenshots/*.png

Needs tmux, Playwright with Chromium (`playwright install chromium`, or HIVESWARM_E2E_CHROMIUM=/path/to/chrome),
and `hiveswarm-mcp` on PATH (it is, inside the project's virtualenv) for the chat agent and the lead.

It drives the app the way a person does: the window opens on the Home chat, the chat agent starts a project and the
view follows it, a busy project's workspace tiles its agents, and the Control tab holds the rest.
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


WIDTH, HEIGHT, SCALE = 1600, 960, 1.25


def attention(stack: Stack, tid: str) -> str | None:
    return (stack.session(tid).get("attention") or {}).get("kind")


def open_window(play, exe: str | None):
    """A window whose page is WIDTH x HEIGHT at a real device scale factor. Playwright's own `device_scale_factor`
    is an emulation under which Chromium reports device-pixel sizes in CSS pixels, and the terminals' WebGL canvases
    then come out the wrong size; a scale factor the browser itself was started with does not have that problem."""
    args = [f"--force-device-scale-factor={SCALE}", f"--window-size={WIDTH},{HEIGHT}"]
    browser = play.chromium.launch(executable_path=exe, args=args) if exe else play.chromium.launch(args=args)
    ctx = browser.new_context(no_viewport=True, color_scheme="dark")
    pg = ctx.new_page()
    pg.goto("about:blank")
    cdp = ctx.new_cdp_session(pg)
    win = cdp.send("Browser.getWindowForTarget")["windowId"]
    for _ in range(8):
        w, h = pg.evaluate("[innerWidth, innerHeight]")
        if (w, h) == (WIDTH, HEIGHT):
            break
        b = cdp.send("Browser.getWindowBounds", {"windowId": win})["bounds"]
        cdp.send("Browser.setWindowBounds", {"windowId": win, "bounds": {"width": b["width"] + WIDTH - w, "height": b["height"] + HEIGHT - h}})
        pg.wait_for_timeout(300)
    return browser, pg


def chat_terminal(pg, workspace: str) -> str:
    """Wait until the chat agent's terminal of this workspace is on the page (not the one of the workspace the page
    is just leaving) and has had a moment to connect and draw. Returns the chat agent's id."""
    tile = pg.locator(f"[data-workspace='{workspace}'] [data-tile][data-role=chat]")
    tile.locator("[data-term=attach] .xterm-screen").wait_for(timeout=40000)
    pg.wait_for_timeout(2500)
    return tile.get_attribute("data-tile")


def screen(stack: Stack, tid: str) -> str:
    """What an agent's terminal shows, read from tmux (the page draws it on a canvas)."""
    cid = stack.post(f"/sessions/{tid}/send", {"screen": True, "lines": 60})["command_id"]
    for _ in range(40):
        c = stack.get(f"/sessions/commands/{cid}")
        if c.get("done_at"):
            return c.get("result") or ""
        time.sleep(0.25)
    return ""


def ask(stack: Stack, pg, tid: str, text: str) -> None:
    """Type a line into the chat agent's terminal and wait for its answer."""
    pg.keyboard.type(text)
    pg.keyboard.press("Enter")
    stack.wait_for(lambda: f"You said: {text}" in screen(stack, tid), 60, "the chat agent's answer")


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="hs-shots-"))
    stack = Stack(home)
    venv_bin = Path(sys.executable).parent
    stack.env["PATH"] = f"{venv_bin}{os.pathsep}{stack.env['PATH']}"
    stack.env["FAKE_STEP"] = "0.5"
    wt = home / "worker.toml"
    wt.write_text(wt.read_text().replace("concurrency = 2", "concurrency = 8").replace("concurrency = 1", "concurrency = 3"))
    stack.start()
    ui_port = free_port()
    ui_log = open(home / "ui.log", "ab")
    ui = subprocess.Popen([sys.executable, "-c", f"from hiveswarm.ui_server import main; main(port={ui_port}, open_browser=False)"],
                          env=stack.env, stdout=ui_log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{ui_port}"
    live: list[str] = []
    try:
        # ── history: finished tasks on both agents (the stats, and the chips of finished work) ──
        done = []
        for i, spec in enumerate(["Add a docstring to add()", "Add type hints to add.py", "Add subtract() with a test",
                                  "Write a module docstring", "Add __all__ to add.py", "Add a multiply() with a test"]):
            r = stack.post("/tasks", {"project": "demo", "spec": spec, "acceptance": "python3 -c 'import add'",
                                      "origin": "you", "agent": ["claude_code", "codex"][i % 2]})
            done.append(r["id"])
        for tid in done:
            stack.wait_for(lambda t=tid: stack.state(t) in ("done", "failed"), 120, "history task")
        untested = stack.post("/tasks", {"project": "demo", "spec": "Tidy the imports in add.py [skip tests]", "acceptance": "python3 -c 'import add'",
                                         "origin": "you", "agent": "claude_code"})["id"]
        failing = stack.post("/tasks", {"project": "demo", "spec": "Make the tests fail on purpose", "acceptance": "false", "origin": "you"})["id"]
        stack.wait_for(lambda: stack.state(untested) == "done", 120, "the untested task")
        stack.wait_for(lambda: stack.state(failing) == "failed", 200, "the failing task")
        stack.post("/directives", {"project": "demo", "text": "Never copy code from a repository we do not have permission to copy from.",
                                   "every_tools": 8, "every_minutes": 10, "check": True, "created_by": "you"})
        stack.put("/projects/demo/plan", {"text": "## Plan\n1. divide() that raises on zero\n2. tests for both branches\n3. README section\n\nDecisions: keep add.py a single module.", "by": "lead"})

        # ── a profile, the default one: what a project the chat agent starts runs on ──
        stack.post("/profiles", {"name": "deep", "note": "Opus leads and reviews, two Opus medium lanes, Codex for tests",
                                 "lead": {"adapter": "claude_code", "model": "opus", "effort": "max"},
                                 "coders": [{"adapter": "claude_code", "model": "opus", "effort": "medium", "count": 2},
                                            {"adapter": "codex", "model": "gpt-5.5", "effort": "high", "count": 1}]})
        stack.post("/settings", {"default_profile": "deep", "chat_model": "sonnet"})

        with sync_playwright() as play:
            browser, pg = open_window(play, os.environ.get("HIVESWARM_E2E_CHROMIUM"))
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)))

            def shot(name: str, wait: int = 1200) -> None:
                pg.wait_for_timeout(wait)
                pg.screenshot(path=str(OUT / name))
                print("wrote", name, flush=True)

            # ── the app opens on one terminal: a fresh chat agent ──
            pg.goto(base + "/", wait_until="load")
            home_chat = chat_terminal(pg, "_home")
            ask(stack, pg, home_chat, "What can you do from here?")
            shot("01-home-chat.png", 1500)

            # ── it starts a project; the view follows it and the lead appears beside it ──
            pg.keyboard.type("start orbit: Build a small orbit calculator: period and velocity from altitude, with tests")
            pg.keyboard.press("Enter")
            pg.wait_for_url("**/p/orbit", timeout=40000)
            lead = stack.wait_for(lambda: next((x["lead"] for x in stack.get("/leads") if x["project"] == "orbit" and x["lead"]), None), 30, "orbit's lead")
            live.append(lead["id"])
            stack.wait_for(lambda: attention(stack, lead["id"]) == "question", 90, "the lead's plan question")
            shot("02-project-started.png", 2500)

            # ── a busy project: its lead asks, one agent needs an approval, others work, headless tasks stream ──
            dlead = stack.post("/leads", {"project": "demo", "message": "Add a divide() that raises on zero, with tests"})["id"]
            a = stack.post("/sessions", {"project": "demo", "spec": "Add input validation to the signup form and cover it with tests",
                                         "permission_mode": "auto", "origin": "lead"})["id"]
            b = stack.post("/sessions", {"project": "demo", "spec": "Refactor add.py into a small arithmetic module with a CLI",
                                         "permission_mode": "auto", "agent": "codex", "origin": "lead"})["id"]
            live += [dlead, a, b]
            stack.wait_for(lambda: attention(stack, a) == "permission", 60, "the agent's permission request")
            stack.wait_for(lambda: attention(stack, dlead) == "question", 90, "demo's lead to ask")
            for spec in ["Write the README section for divide()", "Add type hints to the arithmetic module"]:
                live.append(stack.post("/tasks", {"project": "demo", "spec": spec, "acceptance": "sleep 240; python3 -c 'import add'", "origin": "lead"})["id"])
            names = list(stack.get("/projects").keys())
            pg.keyboard.press(f"Control+Shift+Digit{names.index('demo') + 1}")
            pg.wait_for_url("**/p/demo", timeout=10000)
            ask(stack, pg, chat_terminal(pg, "demo"), "What is the lead waiting for?")
            shot("03-workspace.png", 3000)

            pg.locator(f"[data-tile='{b}']").click()
            pg.keyboard.press("Control+Shift+KeyZ")
            shot("04-zoom.png", 1500)
            pg.keyboard.press("Control+Shift+KeyZ")

            pg.locator(f"[data-done-chip='{done[2]}']").click()
            pg.locator(f"[data-detail='{done[2]}'] button", has_text="Diff").click()
            shot("05-details.png", 1500)
            pg.keyboard.press("Escape")

            # ── many agents: two columns of tiles, small terminals as live miniatures, the quietest as title lines ──
            for spec in ["Add a power() with a test", "Add a modulo() with a test", "Document the CLI flags", "Add a changelog entry",
                         "Add a benchmark script", "Check the edge cases of subtract()"]:
                live.append(stack.post("/tasks", {"project": "demo", "spec": spec, "acceptance": "sleep 240; python3 -c 'import add'", "origin": "lead"})["id"])
            for spec in ["Port the test suite to pytest fixtures", "Write the packaging metadata"]:
                live.append(stack.post("/sessions", {"project": "demo", "spec": spec, "permission_mode": "auto", "origin": "lead"})["id"])
            stack.wait_for(lambda: sum(1 for t in stack.get("/tasks", project="demo", limit=100) if t["state"] in ("running", "verifying")) >= 10, 90, "the swarm to fill up")
            shot("06-many-agents.png", 5000)

            # ── the other tab ──
            pg.keyboard.press("Control+Shift+KeyP")
            pg.wait_for_url("**/control", timeout=10000)
            shot("07-control.png", 3000)
            pg.evaluate("document.getElementById('agents').scrollIntoView({block: 'start'})")
            shot("08-control-agents.png", 1200)
            pg.evaluate("document.getElementById('activity').scrollIntoView({block: 'start'})")
            shot("09-control-activity.png", 2500)
            pg.evaluate("document.querySelector('[data-control]').scrollTo(0, 0)")
            pg.locator("[data-project-settings=demo]").click()
            pg.locator("[data-project-dialog=demo] [data-plan] button").click()
            shot("10-project-settings.png", 1500)
            pg.keyboard.press("Escape")

            pg.evaluate("localStorage.setItem('hm.theme','light')")
            pg.goto(base + "/p/orbit", wait_until="load")
            chat_terminal(pg, "orbit")
            shot("11-workspace-light.png", 2500)
            pg.evaluate("localStorage.setItem('hm.theme','dark')")
            browser.close()
            if errors:
                print("page errors:", errors)
        for tid in live:
            try:
                stack.post(f"/tasks/{tid}/cancel")
            except Exception:
                pass
        time.sleep(1)
        return 0
    except Exception:
        for t in stack.get("/tasks", limit=12):
            if t.get("kind") == "session":
                print(f"--- {t['id']} ({t['state']}):\n" + stack.log_text(t["id"])[-1500:])
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
