from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

_CRLF = re.compile(r"\r\n?")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

TOOLS = "tools"
LOOP = "agent_loop"

LogSink = Callable[[str, str], None] | None
Parser = Callable[[str], list[tuple[str, str]]]

SANDBOX: list[str] = []
HOOK_SETTINGS: Any = None

_local = threading.local()


def cancel_event() -> threading.Event:
    ev = getattr(_local, "cancel", None)
    if ev is None:
        ev = threading.Event()
        _local.cancel = ev
    return ev


def _clip(text: str, lines: int = 6, width: int = 400) -> str:
    rows = [r[:width] for r in (text or "").rstrip().splitlines()]
    if len(rows) > lines:
        return "\n".join(rows[:lines]) + f"\n… (+{len(rows) - lines} more lines)"
    return "\n".join(rows)


def _rel(text: str, cwd: str | None) -> str:
    if not cwd:
        return text
    return text.replace(cwd.rstrip("/") + "/", "").replace(cwd.rstrip("/"), ".")


def _tool_summary(name: str, inp: dict[str, Any], cwd: str | None = None) -> str:
    if not isinstance(inp, dict):
        return f"{name}: {str(inp)[:160]}"
    if name in ("TodoWrite", "todo", "todoToolCall", "updateTodos"):
        todos = inp.get("todos") or []
        return f"{name}: {len(todos)} items"
    for key in ("command", "cmd", "file_path", "path", "filePath", "pattern", "globPattern", "url", "query",
                "description", "prompt", "subject"):
        v = inp.get(key)
        if v:
            first = str(v).strip().splitlines()[0] if str(v).strip() else ""
            return f"{name}: {_rel(first, cwd)[:200]}"
    return f"{name}: {_rel(json.dumps(inp), cwd)[:200]}"


def _fmt(kind: str, text: str) -> str:
    prefix = {"msg": "", "tool": "> ", "result": "  = ", "err": "! ", "info": "# ", "think": "  ~ "}.get(kind, "")
    return "\n".join(prefix + line for line in text.splitlines()) + "\n"


def claude_parser(meta: dict[str, Any], cwd: str | None = None) -> Parser:
    def parse(line: str) -> list[tuple[str, str]]:
        s = line.strip()
        if not s:
            return []
        try:
            ev = json.loads(s)
        except json.JSONDecodeError:
            return [("out", s)]
        t = ev.get("type")
        if t == "system":
            st = ev.get("subtype")
            if st == "init":
                effort = f" · effort {ev['effort']}" if ev.get("effort") else ""
                return [("info", f"session started · model {ev.get('model', '?')}{effort}")]
            if st == "api_retry":
                return [("err", f"API retry {ev.get('attempt')}/{ev.get('max_retries')}: {ev.get('error')}")]
            if st == "permission_denied":
                return [("err", f"permission denied: {json.dumps(ev)[:200]}")]
            return []
        if t == "assistant":
            out: list[tuple[str, str]] = []
            for b in (ev.get("message") or {}).get("content") or []:
                bt = b.get("type")
                if bt == "text" and (b.get("text") or "").strip():
                    out.append(("msg", b["text"].strip()))
                elif bt == "tool_use":
                    out.append(("tool", _tool_summary(b.get("name") or "tool", b.get("input") or {}, cwd)))
                elif bt == "thinking" and (b.get("thinking") or "").strip():
                    out.append(("think", _clip(b["thinking"], 3, 240)))
            return out
        if t == "user":
            out = []
            content = (ev.get("message") or {}).get("content")
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_result":
                        continue
                    c = b.get("content")
                    if isinstance(c, list):
                        c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                    text = _clip(str(c or "(no output)"), 4)
                    out.append(("err" if b.get("is_error") else "result", text))
            return out
        if t == "result":
            usage = ev.get("usage") or {}
            meta["tokens_in"] = (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0) \
                + (usage.get("cache_creation_input_tokens") or 0)
            meta["tokens_out"] = usage.get("output_tokens") or 0
            meta["cost_usd"] = ev.get("total_cost_usd")
            secs = (ev.get("duration_ms") or 0) / 1000
            cost = f" · ${ev['total_cost_usd']:.3f}" if ev.get("total_cost_usd") else ""
            out = [("info", f"finished ({ev.get('subtype', '?')}) · {ev.get('num_turns', '?')} turns · {secs:.0f}s{cost}")]
            if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
                out.append(("err", _clip(str(ev.get("result") or ""), 8)))
            return out
        return []
    return parse


def cursor_parser(meta: dict[str, Any], cwd: str | None = None) -> Parser:
    def parse(line: str) -> list[tuple[str, str]]:
        s = line.strip()
        if not s:
            return []
        try:
            ev = json.loads(s)
        except json.JSONDecodeError:
            return [("out", s)]
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            return [("info", f"session started · model {ev.get('model', '?')}")]
        if t == "assistant":
            out = []
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "text" and (b.get("text") or "").strip():
                    out.append(("msg", b["text"].strip()))
            return out
        if t == "tool_call":
            call = ev.get("tool_call") or {}
            if not call:
                return []
            key, body = next(iter(call.items()))
            name = key[:-8] if key.endswith("ToolCall") else key
            body = body if isinstance(body, dict) else {}
            if ev.get("subtype") == "started":
                return [("tool", _tool_summary(name, body.get("args") or {}, cwd))]
            if ev.get("subtype") == "completed":
                res = body.get("result") or {}
                if "success" in res:
                    ok = res["success"] if isinstance(res["success"], dict) else {}
                    bits = []
                    if "exitCode" in ok:
                        bits.append(f"exit {ok['exitCode']}")
                    for k in ("stdout", "content"):
                        if ok.get(k):
                            bits.append(_clip(str(ok[k]), 3))
                            break
                    for k in ("linesCreated", "linesAdded", "totalLines"):
                        if k in ok:
                            bits.append(f"{k} {ok[k]}")
                    return [("result", " · ".join(bits) if bits else "ok")]
                if res:
                    return [("err", _clip(json.dumps(res), 4))]
            return []
        if t == "result":
            secs = (ev.get("duration_ms") or 0) / 1000
            out = [("info", f"finished ({ev.get('subtype', '?')}) · {secs:.0f}s")]
            if ev.get("is_error"):
                out.append(("err", _clip(str(ev.get("result") or ""), 8)))
            return out
        return []
    return parse


_CODEX_TS = re.compile(r"^\[\d{4}-\d{2}-\d{2}T[^\]]*\]\s*")
_CODEX_DONE = re.compile(r"(succeeded|exited \d+|failed)( in [\d.]+m?s)?:?\s*$")


_DIFF_LINE = ("diff --git", "index ", "--- ", "+++ ", "@@", "new file mode", "deleted file mode",
              "similarity index", "rename from", "rename to", "old mode", "new mode")


def _diff_state(meta: dict[str, Any], s: str) -> list[tuple[str, str]] | None:
    if s.startswith("diff --git"):
        meta["in_diff"] = True
        name = s.split(" b/", 1)[-1] if " b/" in s else s[11:]
        return [("result", f"diff: {name}")]
    if meta.get("in_diff"):
        if s.startswith(_DIFF_LINE) or s[:1] in ("+", "-", " ") or s == "":
            return []
        meta["in_diff"] = False
    return None


def codex_parser(meta: dict[str, Any], cwd: str | None = None) -> Parser:
    state = {"mode": "header", "dashes": 0}
    meta.setdefault("msgs", set())
    markers = {"user", "codex", "thinking", "exec", "tokens used", "user instructions:"}

    def parse(line: str) -> list[tuple[str, str]]:
        s = _CODEX_TS.sub("", line.rstrip())
        low = s.strip().lower()
        if state["mode"] == "header":
            if s.strip().startswith("--------"):
                state["dashes"] += 1
                if state["dashes"] >= 2:
                    state["mode"] = "out"
                return []
            if low.startswith("model:"):
                return [("info", f"session started · {s.strip()}")]
            if state["dashes"] == 0 and low and not low.startswith(("openai codex", "reading additional input")):
                state["mode"] = "out"
            else:
                return []
        if not s.strip():
            return []
        if low in markers or low.startswith("user instructions"):
            meta["in_diff"] = False
            state["mode"] = {"user": "skip", "user instructions:": "skip", "codex": "msg", "thinking": "think",
                             "exec": "exec", "tokens used": "tokens"}.get(low, "skip")
            return []
        d = _diff_state(meta, s)
        if d is not None:
            return d
        if state["mode"] == "tokens":
            state["mode"] = "out"
            digits = re.sub(r"[^\d]", "", s)
            if digits:
                meta["tokens_out"] = int(digits)
                return [("info", f"tokens used: {s.strip()}")]
        if low.startswith("tokens used"):
            digits = re.sub(r"[^\d]", "", s)
            if digits:
                meta["tokens_out"] = int(digits)
            state["mode"] = "out"
            return [("info", s.strip())]
        if low.startswith("exec "):
            state["mode"] = "result"
            return [("tool", _rel(s.strip()[5:], cwd))]
        if low.startswith(("apply_patch", "apply patch", "file update")):
            state["mode"] = "result"
            return [("tool", "apply patch")]
        if _CODEX_DONE.search(s) and state["mode"] in ("exec", "result", "out"):
            state["mode"] = "result"
            return [("result", s.strip())]
        mode = state["mode"]
        if mode == "skip":
            return []
        if mode == "exec":
            state["mode"] = "result"
            return [("tool", _rel(re.sub(r" in /\S+$", "", s.strip()), cwd))]
        if mode == "think":
            return [("think", s.strip())]
        if mode == "msg":
            meta["msgs"].add(s.strip())
            return [("msg", _rel(s.strip(), cwd))]
        if mode == "result":
            return [("result", _rel(s.rstrip(), cwd))]
        return [("out", s.rstrip())]
    return parse


def codex_stdout(meta: dict[str, Any]) -> Parser:
    meta.setdefault("msgs", set())
    local: dict[str, Any] = {}

    def parse(line: str) -> list[tuple[str, str]]:
        s = line.rstrip()
        d = _diff_state(local, s)
        if d is not None:
            return []
        s = s.strip()
        if not s or s in meta["msgs"]:
            return []
        return [("msg", s)]
    return parse


def _text_of(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "\n".join(_text_of(x) for x in v)
    if isinstance(v, dict):
        if v.get("type") == "text" or "text" in v:
            return str(v.get("text") or "")
        for k in ("content", "output", "stdout", "result", "message"):
            if k in v:
                return _text_of(v[k])
        return json.dumps(v)[:400]
    return str(v)


def prime_parser(cwd: str | None = None) -> Parser:
    def parse(line: str) -> list[tuple[str, str]]:
        s = line.strip()
        if not s:
            return []
        try:
            ev = json.loads(s)
        except json.JSONDecodeError:
            return [("out", s)]
        if not isinstance(ev, dict):
            return [("out", s)]
        t = ev.get("type")
        if t == "agent_start":
            return [("info", "session started")]
        if t == "tool_execution_start":
            return [("tool", _tool_summary(ev.get("toolName") or "tool", ev.get("args") or {}, cwd))]
        if t == "tool_execution_end":
            text = _clip(_rel(_text_of(ev.get("result")), cwd), 4) or "(no output)"
            return [("err" if ev.get("isError") else "result", text)]
        if t == "message_end":
            msg = ev.get("message") or {}
            if msg.get("role") not in (None, "assistant"):
                return []
            content = msg.get("content")
            if isinstance(content, str):
                return [("msg", content.strip())] if content.strip() else []
            out: list[tuple[str, str]] = []
            for b in content or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and (b.get("text") or "").strip():
                    out.append(("msg", _rel(b["text"].strip(), cwd)))
                elif b.get("type") == "thinking" and (b.get("thinking") or "").strip():
                    out.append(("think", _clip(b["thinking"], 3, 240)))
            return out
        if t == "auto_retry_start":
            return [("err", f"model call failed, retrying ({ev.get('attempt')}/{ev.get('maxAttempts')}): "
                            f"{str(ev.get('errorMessage') or '')[:200]}")]
        if t == "compaction_start":
            return [("info", f"compacting context ({ev.get('reason')})")]
        if t == "agent_end":
            return [("info", "finished")]
        return []
    return parse


def changes_summary(wt: str) -> str:
    try:
        r = subprocess.run(["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True, timeout=20)
    except Exception:
        return ""
    rows = [x for x in r.stdout.splitlines() if x.strip()]
    if not rows:
        return ""
    shown = ", ".join(x.strip() for x in rows[:8])
    return shown + (f" (+{len(rows) - 8} more)" if len(rows) > 8 else "")


def watch_changes(wt: str, emit: Callable[[str, str], None], stop: threading.Event, every: float = 15.0) -> None:
    last = changes_summary(wt)
    while not stop.wait(every):
        now = changes_summary(wt)
        if now and now != last:
            emit("info", f"files: {now}")
        last = now


def _pump(stream: Any, kind: str, sink: list[str], emit: LogSink, parser: Parser | None) -> None:
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            clean = _ANSI.sub("", _CRLF.sub("\n", line)).rstrip("\n")
            events = parser(clean) if parser else ([(kind, clean)] if clean.strip() else [])
            for k, text in events:
                sink.append(_fmt(k, text))
                if emit:
                    try:
                        emit(k, text)
                    except Exception:
                        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _run(cmd: list[str], cwd: str, env: dict[str, str] | None, timeout: int, stdin: str | None = None,
         emit: LogSink = None, parser: Parser | None = None, stderr_kind: str = "err",
         stderr_parser: Parser | None = None) -> dict[str, Any]:
    full_env = {**os.environ, **(env or {})}
    if SANDBOX:
        cmd = [*SANDBOX, *cmd]
    cancel = cancel_event()
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=full_env, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
                                errors="replace")
    except FileNotFoundError:
        return {"ok": False, "outcome": "error", "log": f"{cmd[0]} not found on PATH"}
    if stdin is not None and proc.stdin:
        try:
            proc.stdin.write(stdin)
            proc.stdin.close()
        except Exception:
            pass
    out: list[str] = []
    err: list[str] = []
    t1 = threading.Thread(target=_pump, args=(proc.stdout, "out", out, emit, parser), daemon=True)
    t2 = threading.Thread(target=_pump, args=(proc.stderr, stderr_kind, err, emit, stderr_parser), daemon=True)
    t1.start(); t2.start()
    deadline = time.monotonic() + timeout
    while True:
        try:
            proc.wait(timeout=1)
            break
        except subprocess.TimeoutExpired:
            if cancel.is_set():
                proc.kill()
                proc.wait()
                t1.join(timeout=3); t2.join(timeout=3)
                return {"ok": False, "outcome": "cancelled", "log": ("".join(out) + "".join(err))[-4000:]}
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                t1.join(timeout=3); t2.join(timeout=3)
                return {"ok": False, "outcome": "timeout", "log": ("".join(out) + "".join(err))[-4000:]}
    t1.join(timeout=3); t2.join(timeout=3)
    log = ("".join(out) + "\n" + "".join(err))[-6000:]
    return {"ok": proc.returncode == 0, "outcome": "done" if proc.returncode == 0 else "error", "log": log, "exit": proc.returncode}


WIKI_NOTE = ("This repo keeps a team wiki in .wiki/ — read .wiki/README.md and the notes that concern your task before "
             "starting, and when you finish append the non-obvious things you learned (gotchas, commands, decisions) "
             "as a few lines under a dated heading in .wiki/learnings.md.")


def ruleset_text(task: dict[str, Any]) -> str:
    """The ruleset block the daemon attached to this task's claim (see rulesets.py), or ""."""
    rs = task.get("ruleset")
    if isinstance(rs, dict):
        return str(rs.get("text") or "")
    return ""


def _prompt(task: dict[str, Any], handoff: str, wt: str | None = None) -> str:
    parts = [handoff, "## Task", task["spec"]]
    if task.get("acceptance"):
        parts.append(f"\n## Acceptance\nThe following must exit 0 when you are done:\n`{task['acceptance']}`")
    parts.append("\nWork in the current directory. Make the change, run the acceptance command if you can, "
                 "and stop when it passes. Do not commit; the harness commits for you. "
                 "You have internet access: if a tool, library, or test runner is missing, install it "
                 "(pip install --user, npm install, cargo add, and so on). "
                 "Never replace the real test runner with a stub, mock, or wrapper script.")
    rules = ruleset_text(task)
    if rules:
        parts.append("\n" + rules)
    if wt and os.path.isdir(os.path.join(wt, ".wiki")):
        parts.append(WIKI_NOTE)
    return "\n".join(p for p in parts if p)


def install_skills(config_dir: str, skills: dict[str, str]) -> None:
    """Write Claude Code skills (name -> SKILL.md) into a config dir; rewrites only when the text changed."""
    import logging
    for name, text in skills.items():
        try:
            d = os.path.join(config_dir, "skills", name)
            os.makedirs(d, exist_ok=True)
            f = os.path.join(d, "SKILL.md")
            cur = open(f).read() if os.path.exists(f) else None
            if cur != text:
                with open(f, "w") as fh:
                    fh.write(text)
        except Exception as e:
            logging.getLogger("hiveswarm.adapters").warning("could not install the %s skill: %s", name, e)


def claude_code(task: dict[str, Any], wt: str, handoff: str, cfg: dict[str, Any], emit: LogSink = None) -> dict[str, Any]:
    from . import claude_auth
    env = claude_auth.auth_env(cfg)
    try:
        claude_auth.seed_config(env["CLAUDE_CONFIG_DIR"], [wt])
    except Exception:
        pass
    if ruleset_text(task):
        from .. import rulesets
        install_skills(env["CLAUDE_CONFIG_DIR"], {"hiveswarm-craft": rulesets.CRAFT_SKILL_MD})
    cmd = ["claude", "-p", _prompt(task, handoff, wt), "--output-format", "stream-json", "--verbose"]
    if HOOK_SETTINGS is not None:
        try:
            cmd += ["--settings", HOOK_SETTINGS(task["id"])]
        except Exception:
            pass
    if cfg.get("dangerously_skip_permissions"):
        cmd.append("--dangerously-skip-permissions")
    else:
        cmd += ["--permission-mode", cfg.get("permission_mode", "acceptEdits")]
    if cfg.get("model"):
        cmd += ["--model", cfg["model"]]
    if cfg.get("effort"):
        cmd += ["--effort", str(cfg["effort"])]
    meta: dict[str, Any] = {}
    res = _run(cmd, wt, env, int(cfg.get("timeout", 1800)), emit=emit, parser=claude_parser(meta, wt))
    res.update({k: v for k, v in meta.items() if v is not None})
    return res


def codex(task: dict[str, Any], wt: str, handoff: str, cfg: dict[str, Any], emit: LogSink = None) -> dict[str, Any]:
    sandbox = cfg.get("sandbox", "workspace-write")
    cmd = ["codex", "exec", "--sandbox", sandbox]
    if cfg.get("effort"):
        cmd += ["-c", f'model_reasoning_effort="{cfg["effort"]}"']
    cmd.append(_prompt(task, handoff, wt))
    if cfg.get("model"):
        cmd += ["--model", cfg["model"]]
    meta: dict[str, Any] = {}
    res = _run(cmd, wt, None, int(cfg.get("timeout", 1800)), emit=emit, stderr_kind="out",
               parser=codex_stdout(meta), stderr_parser=codex_parser(meta, wt))
    if meta.get("tokens_out"):
        res["tokens_out"] = meta["tokens_out"]
    return res


def gemini(task: dict[str, Any], wt: str, handoff: str, cfg: dict[str, Any], emit: LogSink = None) -> dict[str, Any]:
    cmd = ["gemini", "-p", _prompt(task, handoff, wt)]
    if cfg.get("yolo", True):
        cmd.append("--yolo")
    if cfg.get("model"):
        cmd += ["-m", cfg["model"]]
    return _run(cmd, wt, None, int(cfg.get("timeout", 1800)), emit=emit)


def antigravity(task: dict[str, Any], wt: str, handoff: str, cfg: dict[str, Any], emit: LogSink = None) -> dict[str, Any]:
    inner = ["agy", "-p", _prompt(task, handoff, wt), cfg.get("add_dir_flag", "--add-dir"), wt]
    if cfg.get("dangerously_skip_permissions", True):
        inner.append("--dangerously-skip-permissions")
    if cfg.get("sandbox"):
        inner.append("--sandbox")
    if cfg.get("model"):
        inner += ["--model", cfg["model"]]
    if cfg.get("pty", True) and shutil.which("script"):
        cmd = ["script", "-qec", shlex.join(inner), "/dev/null"]
    else:
        cmd = inner
    return _run(cmd, wt, None, int(cfg.get("timeout", 1800)), stdin="", emit=emit)


def cursor(task: dict[str, Any], wt: str, handoff: str, cfg: dict[str, Any], emit: LogSink = None) -> dict[str, Any]:
    binary = cfg.get("binary") or ("cursor-agent" if shutil.which("cursor-agent") else "agent")
    cmd = [binary, "-p", _prompt(task, handoff, wt), "--output-format", "stream-json"]
    if cfg.get("force"):
        cmd.insert(1, "--force")
    if cfg.get("model"):
        cmd += ["--model", cfg["model"]]
    meta: dict[str, Any] = {}
    return _run(cmd, wt, None, int(cfg.get("timeout", 1800)), emit=emit, parser=cursor_parser(meta, wt))


def opencode(task: dict[str, Any], wt: str, handoff: str, cfg: dict[str, Any], emit: LogSink = None) -> dict[str, Any]:
    cmd = ["opencode", "run", _prompt(task, handoff, wt)]
    if cfg.get("model"):
        cmd += ["--model", cfg["model"]]
    return _run(cmd, wt, None, int(cfg.get("timeout", 1800)), emit=emit)


ADAPTERS: dict[str, Any] = {
    "claude_code": claude_code,
    "codex": codex,
    "gemini": gemini,
    "antigravity": antigravity,
    "cursor": cursor,
    "opencode": opencode,
}

CAPABILITIES: dict[str, list[str]] = {
    "claude_code": [TOOLS, LOOP],
    "codex": [TOOLS, LOOP],
    "gemini": [TOOLS, LOOP],
    "antigravity": [TOOLS, LOOP, "vision"],
    "cursor": [TOOLS, LOOP],
    "opencode": [TOOLS, LOOP],
}

# Adapters whose CLI takes a reasoning-effort setting; the others ignore `effort` (the worker says so once).
EFFORT_FLAGS: dict[str, str] = {"claude_code": "--effort", "codex": "-c model_reasoning_effort="}

# What `model` may be set to, per adapter, for the Agents page's picker. Claude Code and Codex publish aliases and
# ids but no way to list them; the three CLIs in MODEL_LIST_ARGS print their own list (`<id> …` per line), which the
# app reads at most every ten minutes. Any other string still works: the picker has a free-text entry.
MODELS: dict[str, list[str]] = {
    "claude_code": ["best", "fable", "opus", "sonnet", "haiku", "opusplan", "sonnet[1m]", "opus[1m]", "opusplan[1m]",
                    "claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5-20251001"],
    "codex": ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5-codex"],
    "gemini": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"],
}
MODEL_LIST_ARGS: dict[str, list[str]] = {
    "cursor": ["--list-models"],
    "antigravity": ["--list-models"],
    "opencode": ["models"],
}

BINARIES: dict[str, str] = {
    "claude_code": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "antigravity": "agy",
    "cursor": "cursor-agent",
    "opencode": "opencode",
}


# ── plugins ────────────────────────────────────────────────────────────────
# A third-party adapter is any callable with this signature, registered under the "hiveswarm.adapters" entry-point
# group (see docs/adapters.md):
#     def my_agent(task, wt, handoff, cfg, emit=None) -> dict
# It may carry two attributes: `capabilities` (list of "tools", "agent_loop", "vision") and `binary` (the executable
# whose presence on PATH means the agent is installed). Both default sensibly.

# Public names for plugin authors (stable across 0.x): the CLI runner and the standard task prompt.
run_cli = _run
task_prompt = _prompt

_PLUGINS_LOADED = False


def load_plugins() -> list[str]:
    """Merge adapters registered by other packages into ADAPTERS/CAPABILITIES/BINARIES. Safe to call repeatedly."""
    global _PLUGINS_LOADED
    if _PLUGINS_LOADED:
        return []
    _PLUGINS_LOADED = True
    added: list[str] = []
    try:
        from importlib.metadata import entry_points
        eps = entry_points(group="hiveswarm.adapters")
    except Exception:
        return added
    for ep in eps:
        if ep.name in ADAPTERS and ep.value.startswith("hiveswarm.workers.adapters:"):
            continue
        try:
            fn = ep.load()
        except Exception as e:  # a broken plugin must not take the worker down
            import logging
            logging.getLogger("hiveswarm.adapters").warning("adapter plugin %s failed to load: %s", ep.name, e)
            continue
        ADAPTERS[ep.name] = fn
        CAPABILITIES[ep.name] = list(getattr(fn, "capabilities", [TOOLS, LOOP]))
        BINARIES[ep.name] = str(getattr(fn, "binary", ep.name))
        added.append(ep.name)
    return added
