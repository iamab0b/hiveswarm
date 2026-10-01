from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import lead as _lead
from . import adapters, claude_auth

log = logging.getLogger("hiveswarm.sessions")

TMUX_SOCKET = "hm"
TMUX_SESSION = "hm"

APPROVAL_RE = re.compile(r"(?i)(allow once|allow always|yes, allow|approve\b|\[y/n\]|\(y/n\)|do you want to proceed|"
                         r"press enter to (approve|confirm)|run this command\?)")
QUESTION_RE = re.compile(r"(?i)(what would you like|which option|how should i proceed|please choose|let me know which|"
                         r"which (one|approach) (do you|would you)|select an option)")
LIMIT_RE = re.compile(r"(?i)(usage limit reached|hit your (usage|session|weekly) limit|5-hour limit reached|"
                      r"(session|weekly) limit.*resets|rate limit.*try again)")


REMINDER_GRACE = 90.0  # seconds after a typed reminder during which screen activity is taken as the agent's reply to it


def _prompt_line(nonempty: list[str]) -> tuple[str, int] | None:
    """Find a prompt in the bottom three non-empty screen lines: (kind, index) of the lowest matching line."""
    for idx in range(len(nonempty) - 1, max(-1, len(nonempty) - 4), -1):
        line = nonempty[idx]
        if LIMIT_RE.search(line):
            return "usage_limit", idx
        if APPROVAL_RE.search(line):
            return "permission", idx
        if QUESTION_RE.search(line):
            return "question", idx
    return None


def tmux(*args: str, check: bool = False, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["tmux", "-L", TMUX_SOCKET, *args], capture_output=True, text=True, timeout=timeout, check=check)


_TUNED = False


def tune_server() -> None:
    """Options every viewer relies on: mouse mode (drag selects, wheel scrolls history), selections copied to the
    viewer's clipboard through OSC 52 (tmux's default set-clipboard=external does that once the terminal advertises it), and no tmux right-click menu (the app uses right-click to paste)."""
    global _TUNED
    if _TUNED:
        return
    tmux("set-option", "-g", "history-limit", "20000")
    tmux("set-option", "-g", "mouse", "on")
    tmux("set-option", "-as", "terminal-features", "xterm-256color:clipboard")
    tmux("set-option", "-ga", "terminal-overrides", "xterm-256color:Ms=\\E]52;%p1%s;%p2%s\\007")
    tmux("unbind", "-n", "MouseDown3Pane")
    tmux("unbind", "-n", "M-MouseDown3Pane")
    _TUNED = True


def ensure_server() -> None:
    if tmux("has-session", "-t", TMUX_SESSION).returncode != 0:
        tmux("new-session", "-d", "-s", TMUX_SESSION, "-n", "hive", "-x", "200", "-y", "50")
        tmux("set-option", "-t", TMUX_SESSION, "status-right", " hiveswarm · prefix+d returns to the dashboard ")
    tune_server()


def window_alive(win: str) -> bool:
    r = tmux("display-message", "-p", "-t", win, "#{pane_dead}")
    return r.returncode == 0 and r.stdout.strip() == "0"


def window_exit_code(win: str) -> int | None:
    r = tmux("display-message", "-p", "-t", win, "#{pane_dead_status}")
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def capture(win: str, lines: int = 60) -> str:
    r = tmux("capture-pane", "-p", "-J", "-t", win, "-S", f"-{lines}")
    return r.stdout if r.returncode == 0 else ""


def capture_ansi(win: str, lines: int = 60) -> str:
    r = tmux("capture-pane", "-p", "-e", "-J", "-t", win, "-S", f"-{lines}")
    return r.stdout if r.returncode == 0 else ""


def send_text(win: str, text: str, enter: bool = True) -> None:
    if text:
        tmux("send-keys", "-t", win, "-l", text)
        time.sleep(0.15)
    if enter:
        tmux("send-keys", "-t", win, "Enter")


def send_keys(win: str, keys: list[str]) -> None:
    for k in keys:
        if k in ("Enter", "Escape", "Tab", "Up", "Down", "Left", "Right", "BSpace", "Space", "PageUp", "PageDown",
                 "Home", "End") or k.startswith("C-") or k.startswith("M-"):
            tmux("send-keys", "-t", win, k)
        else:
            tmux("send-keys", "-t", win, "-l", k)
        time.sleep(0.12)


def kill_window(win: str) -> None:
    tmux("kill-window", "-t", win)


HOOK_SCRIPT = '''#!/usr/bin/env python3
import json, sys, urllib.request
port, tid = sys.argv[1], sys.argv[2]
raw = sys.stdin.buffer.read()
try:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/hook/{tid}", data=raw, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=7200) as r:
        body = r.read().decode()
        if body.strip():
            sys.stdout.write(body)
except Exception:
    pass
sys.exit(0)
'''


def hook_settings(script: str, port: int, tid: str, headless: bool = False) -> str:
    """Claude Code --settings JSON wiring every hook to the worker. Headless (claude -p) lanes only get the two
    hooks that carry standing orders; permissions and questions there follow Claude's own non-interactive rules."""
    cmd = f"python3 {shlex.quote(script)} {port} {tid}"
    def h(timeout: int = 60, status: str | None = None, matcher: str | None = None) -> dict[str, Any]:
        entry: dict[str, Any] = {"type": "command", "command": cmd, "timeout": timeout}
        if status:
            entry["statusMessage"] = status
        group: dict[str, Any] = {"hooks": [entry]}
        if matcher:
            group["matcher"] = matcher
        return group
    hooks = {
        "SessionStart": [h(20)],
        "UserPromptSubmit": [h(20)],
        "PostToolUse": [h(20)],
        "PreToolUse": [h(7200, "Waiting for your answer in Hiveswarm…", "AskUserQuestion")],
        "PermissionRequest": [h(7200, "Waiting for approval in Hiveswarm…")],
        "Notification": [h(20)],
        "Stop": [h(20)],
        "StopFailure": [h(20)],
        "SessionEnd": [h(20)],
    }
    if headless:
        hooks = {k: v for k, v in hooks.items() if k in ("UserPromptSubmit", "PostToolUse")}
    return json.dumps({"hooks": hooks, "skipDangerousModePermissionPrompt": True})


@dataclass
class Live:
    tid: str
    agent: str
    wt: str
    mirror: str
    project: str
    attempt_id: str | None
    win: str = ""
    name: str = ""
    permission_mode: str = "auto"
    transcript_path: str | None = None
    finish: threading.Event = field(default_factory=threading.Event)
    handoff: threading.Event = field(default_factory=threading.Event)
    kill: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    pending: dict[str, Any] | None = None
    pending_lock: threading.Lock = field(default_factory=threading.Lock)
    ship: Any = None
    last_activity: float = 0.0
    idle_reported: bool = True
    last_screen: str = ""
    attention_hash: str = ""
    lead: bool = False
    turn: str = "starting"
    idle_reminded: bool = False
    reminded_at: float = 0.0
    screen_pending: str | None = None


class SessionHost:
    def __init__(self, client: Any, cfg: dict[str, Any], root: Path, host: str, hook_port: int = 7791):
        self.client = client
        self.cfg = cfg
        self.root = root
        self.host = host
        self.hook_port = hook_port
        self.live: dict[str, Live] = {}
        self.lock = threading.Lock()
        self._dir_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._dir_state: dict[tuple[str, str], dict[str, float]] = {}
        self._dir_lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self.script = str(root / "bin" / "hm-hook.py")
        Path(self.script).parent.mkdir(parents=True, exist_ok=True)
        Path(self.script).write_text(HOOK_SCRIPT)
        os.chmod(self.script, 0o755)

    def start(self) -> None:
        ensure_server()
        host = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def do_POST(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                parts = self.path.strip("/").split("/")
                out = b""
                if len(parts) == 2 and parts[0] == "hook":
                    try:
                        ev = json.loads(raw.decode() or "{}")
                    except Exception:
                        ev = {}
                    try:
                        resp = host.on_hook(parts[1], ev)
                    except Exception as e:
                        log.warning("hook handler failed: %s", e)
                        resp = None
                    if resp:
                        out = json.dumps(resp).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                if out:
                    self.wfile.write(out)

        self._server = ThreadingHTTPServer(("127.0.0.1", self.hook_port), Handler)
        self.hook_port = int(self._server.server_address[1])
        threading.Thread(target=self._server.serve_forever, daemon=True, name="hook-server").start()
        threading.Thread(target=self._command_loop, daemon=True, name="session-commands").start()
        threading.Thread(target=self._monitor_loop, daemon=True, name="session-monitor").start()
        log.info("session host ready: tmux socket %s, hooks on 127.0.0.1:%d", TMUX_SOCKET, self.hook_port)

    def post_event(self, tid: str, **ev: Any) -> dict[str, Any]:
        ev.setdefault("host", self.host)
        try:
            out = self.client.post(f"/sessions/{tid}/event", ev, timeout=30)
            return out if isinstance(out, dict) else {}
        except Exception as e:
            log.warning("event %s for %s failed: %s", ev.get("kind"), tid, e)
            return {}

    def launch_argv(self, agent: str, acfg: dict[str, Any], live: Live, prompt: str,
                    resume_id: str | None = None) -> tuple[list[str], dict[str, str]]:
        mode = live.permission_mode
        env: dict[str, str] = {}
        adapter = acfg.get("adapter", agent)
        if adapter == "claude_code":
            env.update(claude_auth.auth_env(acfg))
            try:
                claude_auth.seed_config(env["CLAUDE_CONFIG_DIR"], [live.wt])
            except Exception as e:
                log.warning("could not pre-answer Claude's first-run dialogs in %s: %s", env["CLAUDE_CONFIG_DIR"], e)
            pm = {"auto": "auto", "acceptEdits": "acceptEdits", "bypass": "bypassPermissions"}.get(mode, "auto")
            argv = ["claude", "--permission-mode", pm, "--settings", hook_settings(self.script, self.hook_port, live.tid)]
            if live.lead:
                argv += ["--mcp-config", self.lead_mcp_config(live)]
                self.install_lead_skill(env["CLAUDE_CONFIG_DIR"])
            from .. import rulesets
            adapters.install_skills(env["CLAUDE_CONFIG_DIR"], {"hiveswarm-craft": rulesets.CRAFT_SKILL_MD})
            if resume_id:
                argv += ["--resume", resume_id]
            else:
                argv += ["--name", live.name]
            if acfg.get("model"):
                argv += ["--model", acfg["model"]]
            argv.append(prompt)
        elif adapter == "codex":
            if mode == "bypass":
                argv = ["codex", "--sandbox", "danger-full-access", "-a", "never"]
            else:
                argv = ["codex", "--sandbox", "workspace-write", "-a", "on-request"]
            if acfg.get("model"):
                argv += ["--model", acfg["model"]]
            argv.append(prompt)
        elif adapter == "cursor":
            binary = acfg.get("binary") or "cursor-agent"
            argv = [binary]
            if mode == "bypass" or acfg.get("force"):
                argv.append("--force")
            argv.append(prompt)
        elif adapter == "antigravity":
            argv = ["agy", "--add-dir", live.wt]
            if mode == "bypass":
                argv.append("--dangerously-skip-permissions")
            argv.append(prompt)
        elif adapter == "gemini":
            argv = ["gemini"] + (["--yolo"] if mode == "bypass" else []) + ["-i", prompt]
        else:
            custom = getattr(adapters.ADAPTERS.get(adapter), "session_argv", None)
            if custom is not None:
                argv = list(custom(acfg, prompt, mode))
            else:
                binary = acfg.get("binary") or adapters.BINARIES.get(adapter, agent)
                argv = [binary, prompt]
        return argv, env

    def lead_mcp_config(self, live: Live) -> str:
        url = self.cfg.get("daemon_url") or os.environ.get("HIVESWARM_URL") or "http://127.0.0.1:7778"
        token = self.cfg.get("token") or os.environ.get("HIVESWARM_TOKEN") or ""
        binary = shutil.which("hiveswarm-mcp") or "hiveswarm-mcp"
        return json.dumps({"mcpServers": {"hiveswarm": {
            "type": "stdio", "command": binary, "args": [],
            "env": {"HIVESWARM_URL": url, "HIVESWARM_TOKEN": token, "HIVESWARM_ORIGIN": f"lead {live.tid[:8]}"}}}})

    @staticmethod
    def install_lead_skill(config_dir: str) -> None:
        from .. import lead_skills
        adapters.install_skills(config_dir, {"hiveswarm-lead": _lead.SKILL_MD, **lead_skills.SKILLS})

    def run_session(self, agent: str, acfg: dict[str, Any], task: dict[str, Any], sess: dict[str, Any],
                    attempt_id: str | None, wt: Path, mirror: Path, ship: Any, cancel: threading.Event) -> str:
        live = Live(tid=task["id"], agent=agent, wt=str(wt), mirror=str(mirror), project=task["project"],
                    attempt_id=attempt_id, name=sess.get("name") or f"hm-{task['id'][:8]}",
                    permission_mode=sess.get("permission_mode") or "auto", ship=ship, lead=bool(sess.get("lead")))
        resume_id = None
        if sess.get("resume") and sess.get("claude_session_id") and acfg.get("adapter", agent) == "claude_code":
            resume_id = str(sess["claude_session_id"])
        prompt = self._session_prompt(task, sess, resumed=bool(resume_id))
        argv, env = self.launch_argv(agent, acfg, live, prompt, resume_id)
        if adapters.SANDBOX:
            argv = [*adapters.SANDBOX, *argv]
        cmd = shlex.join(argv)
        ensure_server()
        env_args: list[str] = []
        for k, v in env.items():
            env_args += ["-e", f"{k}={v}"]
        r = tmux("new-window", "-d", "-t", TMUX_SESSION, "-n", live.tid[:8], "-c", live.wt, *env_args, "-P", "-F", "#{window_id}", cmd)
        if r.returncode != 0 and env:
            cmd = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items()) + " " + cmd
            r = tmux("new-window", "-d", "-t", TMUX_SESSION, "-n", live.tid[:8], "-c", live.wt, "-P", "-F", "#{window_id}", cmd)
        if r.returncode != 0:
            raise RuntimeError(f"tmux new-window failed: {r.stderr.strip()[:300]}")
        live.win = r.stdout.strip()
        tmux("set-option", "-t", live.win, "remain-on-exit", "on")
        with self.lock:
            self.live[live.tid] = live
        live.last_activity = time.monotonic()
        ship.emit("status", f"launched {agent} in tmux window {live.win} ({live.name}) in {str(wt).replace(str(Path.home()), '~', 1)}")
        if acfg.get("adapter", agent) != "claude_code":
            self.post_event(live.tid, kind="started", tmux=live.win, summary=f"started {agent} in tmux {live.win}")
            live.idle_reported = False
        else:
            self.post_event(live.tid, kind="meta", tmux=live.win, summary=f"launching claude ({live.permission_mode})")
        return self._watch(live, cancel)

    def adopt(self, task: dict[str, Any], sess: dict[str, Any], wt: str, mirror: str, ship: Any,
              cancel: threading.Event) -> str:
        live = Live(tid=task["id"], agent=sess.get("agent") or task.get("claimed_by") or "agent", wt=wt, mirror=mirror,
                    project=task["project"], attempt_id=None, win=sess.get("tmux") or "",
                    name=sess.get("name") or f"hm-{task['id'][:8]}", permission_mode=sess.get("permission_mode") or "auto",
                    transcript_path=sess.get("transcript_path"), ship=ship)
        with self.lock:
            self.live[live.tid] = live
        live.last_activity = time.monotonic()
        live.idle_reported = True
        ship.emit("status", f"worker restarted; re-attached to tmux window {live.win}")
        if live.transcript_path:
            threading.Thread(target=self._tail_transcript, args=(live, True), daemon=True, name=f"tail-{live.tid[:8]}").start()
        return self._watch(live, cancel)

    def _watch(self, live: Live, cancel: threading.Event) -> str:
        outcome = "exited"
        try:
            while True:
                if cancel.is_set() or live.kill.is_set():
                    outcome = "cancelled"
                    break
                if live.finish.is_set():
                    outcome = "finished"
                    break
                if live.handoff.is_set():
                    outcome = "handoff"
                    break
                if not window_alive(live.win):
                    code = window_exit_code(live.win)
                    self.post_event(live.tid, kind="exited", exit_code=code)
                    live.ship.emit("status", f"{live.agent} exited" + (f" with code {code}" if code else ""))
                    outcome = "exited" if code else "finished"
                    break
                time.sleep(1.0)
        finally:
            with self.lock:
                self.live.pop(live.tid, None)
            self._resolve_pending(live, None)
            live.done.set()
        return outcome

    def _session_prompt(self, task: dict[str, Any], sess: dict[str, Any], resumed: bool = False) -> str:
        if resumed and sess.get("lead"):
            note = (sess.get("resume_note") or "").strip()
            return _lead.lead_prompt(note or "Continue where we left off; check hm_status for the state of the swarm.",
                                     task["project"], None, persistent=bool(sess.get("persistent")))
        if resumed:
            note = (sess.get("resume_note") or "").strip()
            parts = ["The user reopened this session in Hiveswarm. The worktree still has your changes."]
            parts.append(("New instructions: " + note) if note else "Pick up where you left off.")
            if task.get("acceptance"):
                parts.append(f"When you are done, this must exit 0: `{task['acceptance']}`.")
            return "\n".join(parts)
        if sess.get("lead"):
            return _lead.lead_prompt(task["spec"], task["project"], task.get("acceptance"), persistent=bool(sess.get("persistent")))
        from .. import directives as _directives
        block = _directives.prompt_block(self.directives_for(task["id"]))
        parts = ([block] if block else []) + [task["spec"].strip()]
        if task.get("acceptance"):
            parts.append(f"\nWhen you are done, this must exit 0: `{task['acceptance']}`. Run it yourself first.")
        parts.append("\nYou are working in a dedicated git worktree that Hiveswarm manages. Do not commit or push; "
                     "Hiveswarm commits when the user finishes the session. Ask the user (AskUserQuestion) before "
                     "making design decisions that were not specified.")
        rules = adapters.ruleset_text(task)
        if rules:
            parts.append("\n" + rules)
        if os.path.isdir(os.path.join(self._wt_for(task), ".wiki")):
            parts.append(adapters.WIKI_NOTE)
        return "\n".join(parts)

    def _wt_for(self, task: dict[str, Any]) -> str:
        with self.lock:
            live = self.live.get(task["id"])
        return live.wt if live else str(self.root / "worktrees" / task["id"])

    def cleanup_window(self, live: Live) -> None:
        if live.win:
            kill_window(live.win)

    # ── standing orders (directives) ───────────────────────────────────

    def directives_for(self, tid: str, max_age: float = 30.0) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self._dir_lock:
            hit = self._dir_cache.get(tid)
            if hit and now - hit[0] < max_age:
                return hit[1]
        try:
            items = self.client.get("/directives", for_task=tid) or []
        except Exception as e:
            log.debug("directives for %s: %s", tid[:8], e)
            items = hit[1] if hit else []
        with self._dir_lock:
            self._dir_cache[tid] = (now, list(items))
            for d in items:
                self._dir_state.setdefault((tid, d["id"]), {"tools": 0.0, "last": now})
        return list(items)

    def _due_directives(self, tid: str, tool_call: bool) -> list[dict[str, Any]]:
        items = self.directives_for(tid)
        if not items:
            return []
        now = time.monotonic()
        due = []
        with self._dir_lock:
            for d in items:
                st = self._dir_state.setdefault((tid, d["id"]), {"tools": 0.0, "last": now})
                if tool_call:
                    st["tools"] += 1
                if st["tools"] >= int(d.get("every_tools") or 8) or now - st["last"] >= int(d.get("every_minutes") or 10) * 60:
                    due.append(d)
                    st["tools"] = 0.0
                    st["last"] = now
        return due

    def _report_fired(self, tid: str, due: list[dict[str, Any]], how: str, screen: str | None = None) -> None:
        def _go() -> None:
            for d in due:
                try:
                    body: dict[str, Any] = {"task_id": tid, "how": how}
                    if screen:
                        body["screen"] = screen[-6000:]
                    self.client.post(f"/directives/{d['id']}/fired", body, timeout=60)
                except Exception as e:
                    log.debug("fired report failed: %s", e)
        threading.Thread(target=_go, daemon=True, name=f"dirfire-{tid[:8]}").start()

    def _directive_hook(self, tid: str, name: str) -> dict[str, Any] | None:
        from .. import directives as _directives
        if name == "UserPromptSubmit":
            items = self.directives_for(tid)
            if not items:
                return None
            return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                           "additionalContext": _directives.prompt_block(items)}}
        if name == "PostToolUse":
            due = self._due_directives(tid, tool_call=True)
            if not due:
                return None
            self._report_fired(tid, due, "hook")
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                           "additionalContext": _directives.reminder_text(due)}}
        return None

    def _typed_reminder(self, live: Live) -> None:
        """Agents without hooks get their reminder typed into the terminal, only while they sit at their own prompt
        (never into a [y/n] or a question, and never mid-turn)."""
        from .. import directives as _directives
        if live.idle_reminded:
            return
        if live.screen_pending:
            return
        nonempty = [ln for ln in capture(live.win, 60).rstrip().splitlines() if ln.strip()]
        hit = _prompt_line(nonempty)
        if hit is not None and hit[1] == len(nonempty) - 1:
            return
        due = self._due_directives(live.tid, tool_call=False)
        if not due:
            return
        screen = "\n".join(nonempty)
        send_text(live.win, _directives.reminder_text(due), True)
        live.idle_reminded = True
        live.reminded_at = time.monotonic()
        live.last_activity = time.monotonic()
        live.idle_reported = False
        live.ship.emit("status", "typed a standing-order reminder into the terminal")
        self._report_fired(live.tid, due, "typed", screen=screen)

    # ── hooks (Claude Code) ────────────────────────────────────────────

    def on_hook(self, tid: str, ev: dict[str, Any]) -> dict[str, Any] | None:
        name = ev.get("hook_event_name") or ""
        if name in ("UserPromptSubmit", "PostToolUse"):
            out = self._directive_hook(tid, name)
            with self.lock:
                live = self.live.get(tid)
            if live is not None:
                live.last_activity = time.monotonic()
                if name == "UserPromptSubmit":
                    live.turn = "working"
                    live.idle_reminded = False
                    self.post_event(tid, kind="turn", turn="working")
            return out
        with self.lock:
            live = self.live.get(tid)
        if live is None:
            return None
        live.last_activity = time.monotonic()
        if name == "SessionStart":
            live.transcript_path = ev.get("transcript_path") or live.transcript_path
            self.post_event(tid, kind="started", tmux=live.win, claude_session_id=ev.get("session_id"),
                            transcript_path=live.transcript_path,
                            summary=f"claude session {str(ev.get('session_id') or '')[:8]} started ({live.permission_mode})")
            if live.transcript_path:
                threading.Thread(target=self._tail_transcript, args=(live,), daemon=True, name=f"tail-{tid[:8]}").start()
            return None
        if name == "Stop":
            live.turn = "idle"
            self.post_event(tid, kind="turn", turn="idle", summary="finished its turn and is waiting for you")
            return None
        if name == "StopFailure":
            self.post_event(tid, kind="turn", turn="idle", summary=f"turn ended with an API error: {str(ev.get('error') or '')[:200]}")
            return None
        if name == "SessionEnd":
            self.post_event(tid, kind="exited", exit_code=0, summary=f"session ended ({ev.get('reason') or 'exit'})")
            return None
        if name == "Notification":
            nt = ev.get("notification_type") or ev.get("matcher") or ""
            msg = str(ev.get("message") or ev.get("title") or "")[:300]
            if nt.startswith("quota_auto_resume"):
                self.post_event(tid, kind="usage_limit", summary=msg or nt)
            elif nt == "agent_completed":
                self.post_event(tid, kind="meta", summary=f"notification: {msg or nt}")
            elif nt == "idle_prompt":
                self.post_event(tid, kind="turn", turn="idle", summary=msg or "waiting for your input")
            elif nt == "agent_needs_input":
                self.post_event(tid, kind="question", question=msg or "needs your input", options=[])
            return None
        if name == "PermissionRequest":
            tool = ev.get("tool_name") or "tool"
            tool_input = ev.get("tool_input")
            out = self.post_event(tid, kind="permission", tool=tool, tool_input=tool_input, can_decide=True)
            if out.get("decision") == "allow":
                return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                               "decision": {"behavior": "allow"}}}
            answer = self._wait_pending(live, {"kind": "permission", "tool": tool})
            if answer is None:
                return None
            if answer.get("choice") == "approve":
                return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}}
            msg = answer.get("text") or "The user declined this action in Hiveswarm. Choose another approach or ask them."
            return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                           "decision": {"behavior": "deny", "message": msg}}}
        if name == "PreToolUse" and ev.get("tool_name") == "AskUserQuestion":
            question, options = self._parse_ask(ev.get("tool_input"))
            self.post_event(tid, kind="question", question=question, options=options, can_decide=True)
            answer = self._wait_pending(live, {"kind": "question", "options": options})
            if answer is None:
                return None
            reason = self._answer_text(answer, options)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": f"The user answered via Hiveswarm: {reason}. "
                                                                       "Continue with that answer; do not ask again."}}
        return None

    @staticmethod
    def _parse_ask(tool_input: Any) -> tuple[str, list[str]]:
        if not isinstance(tool_input, dict):
            return str(tool_input or "question")[:500], []
        qs = tool_input.get("questions")
        if isinstance(qs, list) and qs:
            q0 = qs[0]
            if isinstance(q0, dict):
                opts = q0.get("options") or []
                labels = [o.get("label") if isinstance(o, dict) else str(o) for o in opts]
                header = q0.get("header")
                text = str(q0.get("question") or "")
                if len(qs) > 1:
                    text += f"  (+{len(qs) - 1} more questions)"
                return (f"[{header}] " if header else "") + text, [str(x) for x in labels if x]
            opts = tool_input.get("options") or []
            return str(q0), [str(o.get("label") if isinstance(o, dict) else o) for o in opts]
        return str(tool_input.get("question") or tool_input)[:500], [str(o) for o in (tool_input.get("options") or [])]

    @staticmethod
    def _answer_text(answer: dict[str, Any], options: list[str]) -> str:
        ch = answer.get("choice")
        if ch == "text":
            return str(answer.get("text") or "")
        try:
            idx = int(ch)
            if 0 < idx <= len(options):
                return options[idx - 1]
        except (TypeError, ValueError):
            pass
        return str(answer.get("text") or ch or "")

    def _wait_pending(self, live: Live, what: dict[str, Any]) -> dict[str, Any] | None:
        ev = threading.Event()
        with live.pending_lock:
            live.pending = {"event": ev, "answer": None, **what}
        ev.wait(7000)
        with live.pending_lock:
            p = live.pending
            live.pending = None
        return (p or {}).get("answer")

    def _resolve_pending(self, live: Live, answer: dict[str, Any] | None) -> bool:
        with live.pending_lock:
            p = live.pending
            if not p:
                return False
            p["answer"] = answer
            p["event"].set()
        return True

    # ── transcript tail (Claude Code JSONL) ────────────────────────────

    def _tail_transcript(self, live: Live, from_end: bool = False) -> None:
        path = live.transcript_path
        if not path:
            return
        parser = adapters.claude_parser({}, live.wt)
        pos = os.path.getsize(path) if (from_end and os.path.exists(path)) else 0
        deadline = time.monotonic() + 60
        while not os.path.exists(path):
            if live.done.is_set() or time.monotonic() > deadline:
                return
            time.sleep(1)
        while not live.done.is_set():
            try:
                with open(path, "rb") as f:
                    f.seek(pos)
                    while True:
                        raw = f.readline()
                        if not raw or not raw.endswith(b"\n"):
                            break
                        pos += len(raw)
                        self._transcript_line(live, raw.decode("utf-8", errors="replace"), parser)
            except FileNotFoundError:
                pass
            except Exception as e:
                log.debug("tail error: %s", e)
            time.sleep(1.0)

    def _transcript_line(self, live: Live, line: str, parser: Any) -> None:
        try:
            d = json.loads(line)
        except Exception:
            return
        if not isinstance(d, dict) or d.get("isSidechain"):
            return
        t = d.get("type")
        if t == "user":
            content = (d.get("message") or {}).get("content")
            if isinstance(content, str) and content.strip():
                text = content.strip()
                if text.startswith("<") and "system-reminder" in text[:40]:
                    return
                live.ship.emit("you", text[:600])
                return
        if t not in ("assistant", "user"):
            return
        for kind, text in parser(json.dumps(d)):
            live.ship.emit(kind, text)
        live.last_activity = time.monotonic()

    # ── commands from the daemon ───────────────────────────────────────

    def _command_loop(self) -> None:
        while True:
            try:
                out = self.client.get("/sessions/commands", host=self.host, wait=20)
                for c in (out or {}).get("commands", []):
                    try:
                        res = self._handle_command(c)
                    except Exception as e:
                        res = f"error: {e}"
                        log.warning("command %s failed: %s", c.get("kind"), e)
                    try:
                        self.client.post(f"/sessions/commands/{c['id']}/done", {"result": res}, timeout=15)
                    except Exception:
                        pass
            except Exception as e:
                log.warning("command poll failed: %s", e)
                time.sleep(3)

    def _handle_command(self, c: dict[str, Any]) -> str:
        tid = c["task_id"]
        with self.lock:
            live = self.live.get(tid)
        if live is None:
            return "no such live session on this host"
        kind = c["kind"]
        p = c.get("payload") or {}
        if kind in ("send", "keys", "answer_option", "answer_other"):
            live.screen_pending = None
            live.idle_reminded = False
        if kind == "send":
            if self._resolve_pending(live, {"choice": "text", "text": p.get("text", "")}):
                return "answered pending question"
            send_text(live.win, p.get("text", ""), bool(p.get("enter", True)))
            live.last_activity = time.monotonic()
            return "sent"
        pending_kind = (live.pending or {}).get("kind")
        if kind == "keys":
            keys = [str(k) for k in (p.get("keys") or [])]
            if pending_kind == "permission" and keys in (["1", "Enter"], ["Enter"], ["y", "Enter"]):
                self._resolve_pending(live, {"choice": "approve"})
                return "approved via hook"
            if pending_kind == "permission" and keys in (["3", "Enter"], ["Down", "Enter"], ["n", "Enter"]):
                self._resolve_pending(live, {"choice": "deny"})
                return "denied via hook"
            if pending_kind == "question":
                return "ignored: a question is pending; answer it with an option or text"
            send_keys(live.win, keys)
            return "keys sent"
        if kind == "answer_option":
            idx = int(p.get("index") or 1)
            if pending_kind == "question" and self._resolve_pending(live, {"choice": str(idx)}):
                return "answered via hook"
            if pending_kind == "permission":
                return "ignored: a permission request is pending; approve or deny it"
            send_keys(live.win, [str(idx), "Enter"])
            return "option keys sent"
        if kind == "answer_other":
            if pending_kind == "question" and self._resolve_pending(live, {"choice": "text", "text": p.get("text", "")}):
                return "answered via hook"
            if pending_kind == "permission":
                self._resolve_pending(live, {"choice": "deny", "text": p.get("text", "")})
                return "denied via hook with your message"
            n = int(p.get("n_options") or 0)
            send_keys(live.win, [str(n + 1), "Enter"])
            send_text(live.win, p.get("text", ""), True)
            return "free-text keys sent"
        if kind == "finish":
            live.finish.set()
            return "finishing"
        if kind == "handoff":
            live.handoff.set()
            return "handing off"
        if kind == "kill":
            live.kill.set()
            return "killing"
        if kind == "screen":
            n = int(p.get("lines") or 50)
            lines = capture(live.win, n).rstrip("\n").splitlines()
            return "\n".join(lines[-n:])[-12000:]
        return f"unknown command {kind}"

    # ── screen monitor (agents without hooks) ─────────────────────────

    def _monitor_loop(self) -> None:
        while True:
            time.sleep(2.0)
            with self.lock:
                items = list(self.live.values())
            for live in items:
                if not live.win:
                    continue
                try:
                    if live.agent == "claude_code":
                        continue
                    self._monitor_one(live)
                    if live.idle_reported:
                        self._typed_reminder(live)
                except Exception as e:
                    log.debug("monitor %s: %s", live.tid[:8], e)

    def _monitor_one(self, live: Live) -> None:
        """Regex screen monitor for agents without hooks: looks at the bottom three non-empty lines for a prompt."""
        screen = capture(live.win, 40)
        if screen != live.last_screen:
            live.last_screen = screen
            live.last_activity = time.monotonic()
            # activity while idle means a new turn — unless it is just the agent replying to the reminder we typed
            if live.idle_reported and time.monotonic() - live.reminded_at > REMINDER_GRACE:
                live.idle_reminded = False
            live.idle_reported = False
        nonempty = [ln for ln in screen.rstrip().splitlines() if ln.strip()]
        tail = "\n".join(nonempty[-12:])
        hit = _prompt_line(nonempty)
        if hit is None:
            live.screen_pending = None
        else:
            kind, idx = hit
            # key on the prompt line and what sits above it, so output appended below an already-handled prompt
            # does not re-trigger it, while a fresh identical prompt (different context above) does
            key = hashlib.md5("\n".join(nonempty[max(0, idx - 2): idx + 1]).encode()).hexdigest()
            if key != live.attention_hash:
                live.attention_hash = key
                live.screen_pending = kind
                line = nonempty[idx]
                if kind == "usage_limit":
                    self.post_event(live.tid, kind="usage_limit", summary=line[:200])
                elif kind == "permission":
                    self.post_event(live.tid, kind="permission", tool="terminal", tool_input={"command": tail.strip()[-600:]},
                                    summary="asks for approval: " + line.strip()[:160])
                else:
                    self.post_event(live.tid, kind="question", question=tail.strip()[-500:], options=[])
                return
        quiet = time.monotonic() - live.last_activity
        if quiet > 10 and not live.idle_reported:
            live.idle_reported = True
            self.post_event(live.tid, kind="turn", turn="idle", summary="no output for 10s — probably waiting for you")
