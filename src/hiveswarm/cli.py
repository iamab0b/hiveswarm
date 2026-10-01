from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx


def _base() -> str:
    return os.environ.get("HIVESWARM_URL", "http://127.0.0.1:7778").rstrip("/")


def _headers() -> dict[str, str]:
    tok = os.environ.get("HIVESWARM_TOKEN")
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _get(path: str, **params: Any) -> Any:
    try:
        r = httpx.get(_base() + path, headers=_headers(), params={k: v for k, v in params.items() if v is not None}, timeout=30)
    except httpx.ConnectError:
        print(f"error: cannot reach daemon at {_base()} — is hiveswarm running?", file=sys.stderr)
        sys.exit(1)
    if r.status_code == 404:
        print(f"error: not found: {path}", file=sys.stderr)
        sys.exit(1)
    if r.status_code == 401:
        print("error: unauthorized — is HIVESWARM_TOKEN set?", file=sys.stderr)
        sys.exit(1)
    if r.status_code >= 400:
        print(f"error {r.status_code}: {r.text[:300]}", file=sys.stderr)
        sys.exit(1)
    return r.json()


def _post(path: str, body: dict[str, Any]) -> Any:
    try:
        r = httpx.post(_base() + path, headers=_headers(), json=body, timeout=30)
    except httpx.ConnectError:
        print(f"error: cannot reach daemon at {_base()} — is hiveswarm running?", file=sys.stderr)
        sys.exit(1)
    if r.status_code == 401:
        print("error: unauthorized — is HIVESWARM_TOKEN set?", file=sys.stderr)
        sys.exit(1)
    if r.status_code >= 400:
        print(f"error {r.status_code}: {r.text[:300]}", file=sys.stderr)
        sys.exit(1)
    return r.json()


def cmd_add(a: argparse.Namespace) -> None:
    if a.file:
        added = 0
        with open(a.file) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                spec, _, acc = line.partition(" || ")
                out = _post("/tasks", {"project": a.project, "spec": spec.strip(),
                                       "acceptance": acc.strip() or a.acceptance, "repo_path": a.repo, "base_ref": a.base})
                print(out["id"], spec[:60])
                added += 1
        print(f"enqueued {added}", file=sys.stderr)
        return
    spec = a.spec if a.spec != "-" else sys.stdin.read()
    out = _post("/tasks", {
        "project": a.project, "spec": spec, "acceptance": a.acceptance,
        "repo_path": a.repo, "base_ref": a.base,
    })
    print(out["id"])


def cmd_list(a: argparse.Namespace) -> None:
    rows = _get("/tasks", project=a.project, state=a.state, limit=a.limit)
    if a.json:
        print(json.dumps(rows, indent=2))
        return
    print(f"{'id':16} {'state':11} {'att':>3} {'agent':12} {'project':12} spec")
    for r in rows:
        first = (r["spec"] or "").strip().splitlines()[0][:50] if r["spec"] else ""
        print(f"{r['id']:16} {r['state']:11} {r['attempts']:>3} {(r['claimed_by'] or '-')[:12]:12} {r['project'][:12]:12} {first}")


def cmd_show(a: argparse.Namespace) -> None:
    out = _get(f"/tasks/{a.id}")
    if a.json:
        print(json.dumps(out, indent=2))
        return
    t, c, atts = out["task"], out["classification"], out["attempts"]
    print(f"task     {t['id']}  state={t['state']}  attempts={t['attempts']}/{t['max_attempts']}")
    print(f"project  {t['project']}  repo={t['repo_path']}  base={t['base_ref']}")
    print(f"worktree {t['worktree']}")
    print(f"spec     {t['spec']}")
    print(f"accept   {t['acceptance']}")
    if c:
        print(f"class    {c['task_type']} (conf {c['type_conf']:.2f})  difficulty {c['difficulty']:.1f} "
              f"(conf {c['difficulty_conf']:.2f})  multistep {c['is_multistep']}  src={c['source']}")
    for x in atts:
        print(f"  attempt {x['id']} {x['agent']:12} {x['outcome'] or '...':8} "
              f"{x['wall_seconds'] or 0:>6.0f}s  tok={x['tokens_in']}/{x['tokens_out']}  {x['diff_stat'] or ''}")
        if a.log and x.get("verifier_log"):
            print("    " + x["verifier_log"].replace("\n", "\n    "))


def cmd_status(a: argparse.Namespace) -> None:
    h = httpx.get(_base() + "/health", timeout=10).json()
    print(json.dumps(h, indent=2))


def cmd_tui(a: argparse.Namespace) -> None:
    try:
        from .tui.app import HiveswarmApp
    except ImportError:
        print("error: textual is not installed — run: uv add textual  (or pip install textual)", file=sys.stderr)
        sys.exit(1)
    HiveswarmApp(url=a.url, poll=a.poll).run()


def cmd_agents(a: argparse.Namespace) -> None:
    if getattr(a, "set", None):
        agent, value = a.set
        cap = None if value in ("auto", "config", "reset") else int(value)
        _post(f"/agents/{agent}/capacity", {"capacity": cap})
        want = "the worker's own config" if cap is None else f"{cap} lanes"
        print(f"{agent}: asked for {want}; the worker applies it within ~10 s (hm agents shows lanes as they change)")
        return
    rows = _get("/agents")
    if not rows:
        print("no agents registered")
        return
    print(f"{'agent':14} {'host':16} {'alive':6} {'busy':8} {'wanted':7} capabilities")
    for r in rows:
        busy = f"{r.get('busy', 0)}/{r.get('capacity', 1)}"
        wanted = "-" if r.get("desired_capacity") is None else str(r["desired_capacity"])
        print(f"{r['agent_id']:14} {(r['host'] or '')[:16]:16} {'yes' if r['alive'] else 'no':6} {busy:8} {wanted:7} {', '.join(r['capabilities'])}")


def cmd_stats(a: argparse.Namespace) -> None:
    t = _get("/stats/agents")
    cells = t.get("cells") or []
    if not cells:
        print("no finished attempts yet")
        return
    print("per agent")
    for ag in t.get("agents") or []:
        prob = "; ".join(f"{p['task_type']}: {p['status']}" for p in ag.get("probation") or [])
        print(f"  {ag['agent']:14} {ag['status']:8} n={ag['n']:<4} pass={_pct(ag.get('pass_rate')):>4}  wall={_secs(ag.get('avg_wall_s')):>7}  step={_secs(ag.get('avg_step_s')):>6}  slow steps={_pct(ag.get('slow_step_rate')):>4}"
              + (f"  probation: {prob}" if prob else ""))
    print()
    print(f"{'agent':14} {'task type':12} {'status':8} {'n':>3} {'pass':>5} {'wall':>7} {'step':>6} {'p90':>6} {'slow':>5} {'trend':>5}  why")
    for c in cells:
        trend = f"{c['trend']:.1f}x" if c.get("trend") is not None else "-"
        print(f"{c['agent']:14} {c['task_type']:12} {c['status']:8} {c['n']:>3} {_pct(c.get('pass_rate')):>5} {_secs(c.get('avg_wall_s')):>7} "
              f"{_secs(c.get('avg_step_s')):>6} {_secs(c.get('p90_step_s')):>6} {_pct(c.get('slow_step_rate')):>5} {trend:>5}  {c.get('why') or ''}")
    print(f"\nslow step = one tool call over {t.get('slow_step_s', 120)}s; slow/failing agents get only easy work of that type until they recover")


def _pct(v: Any) -> str:
    return f"{int(round(v * 100))}%" if isinstance(v, (int, float)) else "-"


def _secs(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return "-"
    if v >= 3600:
        return f"{v / 3600:.1f}h"
    if v >= 90:
        return f"{v / 60:.1f}m"
    return f"{v:.0f}s"


def cmd_inbox(a: argparse.Namespace) -> None:
    items = _get("/inbox").get("items", [])
    if not items:
        print("nothing needs you")
        return
    for it in items:
        att = it.get("attention") or {}
        opts = att.get("options") or []
        print(f"{it['id'][:8]}  {att.get('kind', ''):11} {it.get('agent') or '-':12} {it.get('project', ''):12} {att.get('summary', '')[:100]}")
        for i, o in enumerate(opts, 1):
            print(f"            {i}. {o}")


def cmd_sessions(a: argparse.Namespace) -> None:
    rows = _get("/sessions", limit=a.limit)
    if not rows:
        print("no sessions")
        return
    print(f"{'id':16} {'state':10} {'agent':12} {'turn':9} {'attention':11} spec")
    for r in rows:
        s = r.get("session") or {}
        att = (s.get("attention") or {}).get("kind", "") if r["state"] not in ("done", "failed", "abandoned") else ""
        spec = (r.get("spec") or "").strip().splitlines()[0][:60] if (r.get("spec") or "").strip() else ""
        print(f"{r['id']:16} {r['state']:10} {s.get('agent') or '-':12} {s.get('turn') or '':9} {att:11} {spec}")


def cmd_session(a: argparse.Namespace) -> None:
    tid = a.id
    if a.action == "send":
        print(json.dumps(_post(f"/sessions/{tid}/send", {"text": a.text or ""})))
    elif a.action in ("approve", "deny"):
        print(json.dumps(_post(f"/sessions/{tid}/answer", {"choice": a.action})))
    elif a.action == "answer":
        body = {"choice": a.text} if (a.text or "").isdigit() else {"choice": "text", "text": a.text or ""}
        print(json.dumps(_post(f"/sessions/{tid}/answer", body)))
    elif a.action == "finish":
        print(json.dumps(_post(f"/sessions/{tid}/finish", {})))
    elif a.action == "continue":
        print(json.dumps(_post(f"/sessions/{tid}/continue", {"agent": a.text or "codex"})))
    elif a.action == "new":
        print(json.dumps(_post("/sessions", {"project": a.id, "spec": a.text or "", "agent": a.agent,
                                             "permission_mode": a.permission, "lead": a.lead, "count": a.count,
                                             "origin": "cli"})))


def _delete(path: str) -> Any:
    try:
        r = httpx.delete(_base() + path, headers=_headers(), timeout=30)
    except httpx.ConnectError:
        print(f"error: cannot reach daemon at {_base()} — is hiveswarm running?", file=sys.stderr)
        sys.exit(1)
    if r.status_code >= 400:
        print(f"error {r.status_code}: {r.text[:300]}", file=sys.stderr)
        sys.exit(1)
    return r.json()


def cmd_order(a: argparse.Namespace) -> None:
    if a.action == "list":
        rows = _get("/directives", project=a.project, task_id=a.task)
        if not rows:
            print("no standing orders")
            return
        for d in rows:
            scope = f"lane {d['task_id'][:8]}" if d.get("task_id") else f"project {d.get('project')}"
            last = ""
            if d.get("last_result"):
                try:
                    lr = json.loads(d["last_result"])
                    last = f"  last audit: {'FLAGGED' if lr.get('violated') else 'ok'} ({int(lr.get('p', 0) * 100)}% violation)"
                except Exception:
                    pass
            print(f"{d['id']}  {scope:22} every {d['every_tools']} tools / {d['every_minutes']} min  reminded {d['fires']}x  flags {d['violations']}{last}")
            print(f"    {d['text']}")
    elif a.action == "set":
        if not a.text:
            print("error: give the order text", file=sys.stderr)
            sys.exit(2)
        if not a.task and not a.project:
            print("error: --task <id> or --project <name> is required", file=sys.stderr)
            sys.exit(2)
        r = _post("/directives", {"text": a.text, "task_id": a.task, "project": a.project, "every_tools": a.every_tools,
                                  "every_minutes": a.every_minutes, "check": not a.no_check, "created_by": "cli"})
        print(f"standing order {r['id']} set for {'lane ' + a.task[:8] if a.task else 'every agent in ' + a.project}")
    elif a.action == "clear":
        if not a.text:
            print("error: give the directive id", file=sys.stderr)
            sys.exit(2)
        print(json.dumps(_delete(f"/directives/{a.text}")))
    elif a.action == "unflag":
        if not a.task:
            print("error: --task <id> is required", file=sys.stderr)
            sys.exit(2)
        print(json.dumps(_post(f"/tasks/{a.task}/flags/clear?kind=directive", {})))


def _worker_agent_cfg(agent: str) -> dict[str, Any]:
    try:
        from .workers.remote import _load_cfg
        return (_load_cfg(None).get("agents") or {}).get(agent) or {}
    except Exception:
        return {}


def cmd_login(a: argparse.Namespace) -> None:
    import getpass
    import shutil
    import subprocess

    from .workers import claude_auth

    home = os.path.expanduser("~")
    acfg = _worker_agent_cfg("claude_code")
    if not a.agent:
        st = claude_auth.status(acfg)
        if st["token"]:
            how = "CLAUDE_CODE_OAUTH_TOKEN in the worker's environment" if st["token_source"] == "env" else st["token_file"]
            print(f"claude    signed in for every lane (token: {how})")
        elif st["login_in_config_dir"]:
            print(f"claude    using a /login stored in {st['config_dir']} — it expires; run `hm login claude` for a one-year token")
        else:
            print("claude    NOT signed in — run: claude setup-token, then: hm login claude")
        codex = os.path.isfile(os.path.join(home, ".codex", "auth.json"))
        print(f"codex     {'signed in' if codex else 'not signed in — run: codex login'}")
        gem = any(os.path.isfile(os.path.join(home, ".gemini", f)) for f in ("oauth_creds.json", "google_accounts.json"))
        print(f"gemini    {'signed in' if gem else 'not signed in — run: gemini (once, and pick your login)'}")
        if shutil.which("cursor-agent"):
            try:
                r = subprocess.run(["cursor-agent", "status"], capture_output=True, text=True, timeout=15)
                line = (r.stdout or r.stderr).strip().splitlines()
                print(f"cursor    {line[0][:80] if line else 'unknown'}")
            except Exception:
                print("cursor    unknown — run: cursor-agent status")
        print("\nOnly Claude Code needs the token: its lanes run under their own config dir. The others keep their login in your home directory, which the lanes share.")
        return
    if a.agent == "claude":
        tok = (a.token or "").strip()
        if not tok:
            print("In another terminal run:  claude setup-token")
            print("Approve in the browser (open the URL it prints), then paste the token it shows here.")
            tok = getpass.getpass("Token (hidden): ").strip()
        try:
            path = claude_auth.save_token(tok, acfg)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)
        cdir = claude_auth.config_dir(acfg)
        claude_auth.seed_config(cdir)
        print(f"saved to {path} (mode 600); lanes in {cdir} will use it from their next launch — no worker restart needed")
        if a.no_check:
            return
        print("checking with a one-line headless turn on your subscription…")
        ok, msg = claude_auth.check(acfg)
        print(f"claude: {'signed in' if ok else 'NOT working'} — {msg}")
        if not ok:
            sys.exit(1)
        return
    if a.agent == "codex":
        os.execvp("codex", ["codex", "login"])
    if a.agent == "cursor":
        os.execvp("cursor-agent", ["cursor-agent", "login"])
    if a.agent == "gemini":
        os.execvp("gemini", ["gemini"])


def cmd_project(a: argparse.Namespace) -> None:
    if a.action == "list":
        projects = _get("/projects")
        if not projects:
            print("no projects")
            return
        w = max(len(n) for n in projects)
        for name, p in projects.items():
            loc = p.get("local") or {}
            where = loc.get("path") or "(no local copy yet)"
            note = f"  {loc.get('note')}" if loc.get("note") else ""
            print(f"{name:{w}}  hub: {p.get('repo_path')}  local: {where}{note}")
    elif a.action == "delete":
        if not a.name:
            print("error: which project?", file=sys.stderr)
            sys.exit(2)
        if not a.yes:
            what = "and DELETE its files (the repository on the hub; local copies go to ~/hiveswarm/.trash)" if a.purge else "(its files stay where they are)"
            ans = input(f"remove project {a.name} from Hiveswarm {what}? [y/N] ").strip().lower()
            if ans != "y":
                print("kept")
                return
        print(json.dumps(_delete(f"/projects/{a.name}?purge={'true' if a.purge else 'false'}"), indent=1))


def cmd_sync(a: argparse.Namespace) -> None:
    from .workers.local_copy import LocalCopies
    from .workers.remote import Client, _load_cfg
    cfg = _load_cfg(None)
    client = Client(cfg["daemon_url"], cfg.get("token") or os.environ.get("HIVESWARM_TOKEN"))
    copies = LocalCopies(client, cfg)
    if not copies.enabled:
        print("local copies are off (local_projects = \"\" in worker.toml)")
        return
    projects = client.get("/projects")
    names = [a.project] if a.project else list(projects)
    for n in names:
        if n not in projects:
            print(f"{n}: no such project"); continue
        r = copies.sync_one(n, projects[n], force=True)
        print(f"{n:16} {copies.path_for(n)}  {r.get('note') or ''}")


def cmd_lead(a: argparse.Namespace) -> None:
    r = _post("/leads", {"project": a.project, "message": a.message, "permission_mode": a.permission})
    what = "started" if r.get("created") else ("reopened" if r.get("reopened") else "sent to")
    print(f"{what} the lead for {a.project}: {r['id'][:8]} ({r.get('state')})")
    print(f"watch it: hm ui  →  /lead/{a.project}")


def cmd_ui(a: argparse.Namespace) -> None:
    from .ui_server import main as ui_main
    ui_main(port=a.port, host=a.host, open_browser=not a.no_open)


def cmd_lead_skill(a: argparse.Namespace) -> None:
    from .lead import SKILL_MD
    from .lead_skills import SKILLS
    base = os.path.expanduser(a.dir or os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")
    for name, text in {"hiveswarm-lead": SKILL_MD, **SKILLS}.items():
        d = os.path.join(base, "skills", name)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "SKILL.md")
        with open(path, "w") as f:
            f.write(text)
        print(f"installed {path}")
    print("in Claude Code, type /hiveswarm-lead <goal>  (needs the hiveswarm MCP server: claude mcp add hiveswarm ...)")


def _parse_span(text: str) -> float:
    t = text.strip().lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if t and t[-1] in mult:
        return float(t[:-1]) * mult[t[-1]]
    return float(t) * 60


def cmd_net(a: argparse.Namespace) -> None:
    """What this machine's Hiveswarm processes sent to the daemon and how it went, from ~/.hiveswarm/logs/net-*.jsonl."""
    import time as _time

    from . import netlog
    window = _parse_span(a.since)
    roles = [r.strip() for r in a.role.split(",")] if a.role else None

    def once() -> str:
        events = netlog.read_events(roles, since=_time.time() - window - 3600)
        summary = netlog.summarize(events, window)
        return json.dumps(summary, indent=2) if a.json else netlog.render(summary)

    if not a.watch:
        print(once())
        return
    try:
        while True:
            print("\x1b[2J\x1b[H" + once(), flush=True)
            _time.sleep(float(a.watch))
    except KeyboardInterrupt:
        pass


def cmd_report(a: argparse.Namespace) -> None:
    import subprocess
    import time as _time

    def section(title: str) -> None:
        print(f"\n=== {title}")

    section("hm status")
    try:
        print(json.dumps(httpx.get(_base() + "/health", timeout=10).json(), indent=2))
    except Exception as e:
        print(f"unreachable: {e}")
    section("hm agents")
    try:
        cmd_agents(a)
    except SystemExit:
        pass
    section("hm inbox")
    try:
        cmd_inbox(a)
    except SystemExit:
        pass
    section("hm login")
    try:
        cmd_login(argparse.Namespace(agent=None, token=None, no_check=True))
    except Exception as e:
        print(f"unavailable: {e}")
    section("hm order list")
    try:
        cmd_order(argparse.Namespace(action="list", project=None, task=None))
    except SystemExit:
        pass
    section("hm list")
    rows = _get("/tasks", limit=40)
    for t in rows:
        kind = "session" if t.get("kind") == "session" else "task"
        spec = (t.get("spec") or "").strip().splitlines()[0][:70] if (t.get("spec") or "").strip() else ""
        print(f"{t['id']:16} {kind:7} {t['state']:10} {t.get('attempts', 0)}/{t.get('max_attempts', 0)} {t.get('claimed_by') or '-':12} {t['project']:10} {spec}")
    section("recent problems (failed / abandoned, last 8)")
    bad = [t for t in rows if t["state"] in ("failed", "abandoned")][:8]
    for t in bad:
        d = _get(f"/tasks/{t['id']}")
        print(f"\n--- {t['id']} {t['state']}: {(t.get('spec') or '')[:100]}")
        for x in d.get("attempts", []):
            print(f"  attempt {x.get('agent')} {x.get('outcome')} {x.get('wall_seconds') or 0:.0f}s branch={x.get('branch')}")
            if x.get("verifier_log"):
                print("    " + (x["verifier_log"] or "")[-1500:].replace("\n", "\n    "))
        log = _get(f"/tasks/{t['id']}/log", since=0).get("entries", [])
        print("  last log lines:")
        for e in log[-25:]:
            print(f"    {_time.strftime('%H:%M:%S', _time.localtime(int(e['ts'])))} {e['source']:18} {(e['chunk'] or '')[:160]}")
    section("feed (last 60)")
    for e in _get("/feed", tail=60).get("entries", []):
        print(f"{_time.strftime('%H:%M:%S', _time.localtime(int(e['ts'])))} {e['task_id'][:8]} {e['source']:18} {(e['chunk'] or '')[:160]}")
    section("network (last 2h, this machine)")
    try:
        from . import netlog
        events = netlog.read_events(None, since=_time.time() - 3 * 3600)
        print(netlog.render(netlog.summarize(events, 2 * 3600)))
    except Exception as e:
        print(f"unavailable: {e}")
    section("local")
    for cmd in (["tmux", "-L", "hm", "list-windows", "-t", "hm"], ["systemctl", "--user", "status", "hiveswarm-worker", "--no-pager", "-n", "25"],
                ["systemctl", "status", "hiveswarm", "--no-pager", "-n", "25"], ["docker", "ps", "--format", "{{.Names}} {{.Status}}"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            out = (r.stdout + r.stderr).strip()
            if out:
                print(f"$ {' '.join(cmd)}\n{out}")
        except Exception:
            pass


def cmd_classify(a: argparse.Namespace) -> None:
    from .classify import QUESTIONS
    url = os.environ.get("HIVESWARM_DECIDE_URL")
    if not url:
        try:
            from .config import load
            url = load().get("decide.url", "http://127.0.0.1:9000")
        except Exception:
            url = "http://127.0.0.1:9000"
    r = httpx.post(url.rstrip("/") + "/decide", json={"state": a.text, "questions": QUESTIONS, "bypass_cache": a.no_cache}, timeout=90)
    print(json.dumps(r.json(), indent=2))


def cmd_init(a: argparse.Namespace) -> None:
    from . import setup_cmd
    try:
        setup_cmd.init(a.project, a.repo, a.backend, force=a.force, openai_url=a.openai_url, openai_model=a.openai_model)
    except (FileExistsError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)


def cmd_up(a: argparse.Namespace) -> None:
    from . import setup_cmd
    sys.exit(setup_cmd.up(ui=not a.no_ui, open_browser=not a.no_open, ui_port=a.ui_port))


def cmd_migrate(a: argparse.Namespace) -> None:
    from . import setup_cmd
    setup_cmd.migrate(keep_services=a.keep_services)


def cmd_version(a: argparse.Namespace) -> None:
    from . import __version__
    print(f"hiveswarm {__version__}")


def main() -> None:
    p = argparse.ArgumentParser(prog="hm", description="Hiveswarm CLI")
    p.add_argument("--version", action="store_true", help="print the version and exit")
    sub = p.add_subparsers(dest="cmd", required=False)

    s = sub.add_parser("init", help="write ~/.hiveswarm/config.toml and worker.toml, detect your agents, create a first project")
    s.add_argument("--project", default=None, help="name of the first project (default: demo)")
    s.add_argument("--repo", default=None, help="an existing git repository to use as that project")
    s.add_argument("--backend", default=None, choices=["mock", "openai", "local", "jev"], help="decide backend (default: openai if OPENAI_API_KEY is set, else mock)")
    s.add_argument("--openai-url", default=None, help="OpenAI-compatible base URL for the decide service (default: https://api.openai.com/v1)")
    s.add_argument("--openai-model", default=None)
    s.add_argument("--force", action="store_true", help="overwrite an existing config")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("migrate", help="bring a Hivemind laptop setup (~/.config/hivemind, hivemind-* services) into ~/.hiveswarm; copies, deletes nothing")
    s.add_argument("--keep-services", action="store_true", help="leave the old hivemind-worker / hivemind-ui services running")
    s.set_defaults(fn=cmd_migrate)

    s = sub.add_parser("up", help="run what this machine needs: decide, daemon, worker and app (or only worker and app when the daemon is a remote hub)")
    s.add_argument("--no-ui", action="store_true")
    s.add_argument("--no-open", action="store_true", help="do not open the browser")
    s.add_argument("--ui-port", type=int, default=7790)
    s.set_defaults(fn=cmd_up)

    s = sub.add_parser("add", help="enqueue a task, or many from a file")
    s.add_argument("project")
    s.add_argument("spec", nargs="?", default="", help="task description, or - for stdin")
    s.add_argument("--acceptance", "-a", help="shell command that must exit 0 (default for --file lines without one)")
    s.add_argument("--file", "-f", help="one task per line: SPEC || ACCEPTANCE   (acceptance optional; # comments ok)")
    s.add_argument("--repo", help="override project repo_path")
    s.add_argument("--base", default="HEAD")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("list", help="list tasks")
    s.add_argument("--project")
    s.add_argument("--state")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("show", help="show a task with classification and attempts")
    s.add_argument("id")
    s.add_argument("--log", action="store_true", help="include verifier logs")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("status", help="daemon health")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("stats", help="routing table")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("agents", help="registered agents, their lanes and liveness; --set changes an agent's lanes live")
    s.add_argument("--set", nargs=2, metavar=("AGENT", "LANES"), help="e.g. --set claude_code 5, or --set codex auto for the worker's config")
    s.set_defaults(fn=cmd_agents)

    s = sub.add_parser("tui", help="interactive dashboard")
    s.add_argument("--url", default=None, help="daemon URL (default: $HIVESWARM_URL)")
    s.add_argument("--poll", type=float, default=2.0, help="refresh interval in seconds")
    s.set_defaults(fn=cmd_tui)

    s = sub.add_parser("inbox", help="everything that needs a human: questions, approvals, failures")
    s.set_defaults(fn=cmd_inbox)

    s = sub.add_parser("sessions", help="interactive sessions and their state")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=cmd_sessions)

    s = sub.add_parser("session", help="act on a session: new <project> <prompt> | send|answer|approve|deny|finish|continue <id> [text]")
    s.add_argument("action", choices=["new", "send", "answer", "approve", "deny", "finish", "continue"])
    s.add_argument("id", help="session id (or project name for new)")
    s.add_argument("text", nargs="?", default=None)
    s.add_argument("--agent", default="claude_code")
    s.add_argument("--permission", default="auto", choices=["auto", "acceptEdits", "bypass"])
    s.add_argument("--lead", action="store_true", help="new: make it a lead session that commands the swarm")
    s.add_argument("--count", type=int, default=1)
    s.set_defaults(fn=cmd_session)

    s = sub.add_parser("order", help="standing orders: list | set <text> --task <id>|--project <name> | clear <directive id> | unflag --task <id>")
    s.add_argument("action", choices=["list", "set", "clear", "unflag"])
    s.add_argument("text", nargs="?", default=None, help="the order (set) or the directive id (clear)")
    s.add_argument("--task", default=None, help="lane (task or session id) the order applies to")
    s.add_argument("--project", default=None, help="apply to every current and future agent in this project")
    s.add_argument("--every-tools", type=int, default=8, help="remind Claude Code lanes every N tool calls")
    s.add_argument("--every-minutes", type=int, default=10, help="remind any lane every M minutes")
    s.add_argument("--no-check", action="store_true", help="remind only; do not audit the agent after each reminder")
    s.set_defaults(fn=cmd_order)

    s = sub.add_parser("login", help="sign the agents in once: `hm login` shows who is signed in; `hm login claude` stores a claude setup-token for every lane")
    s.add_argument("agent", nargs="?", choices=["claude", "codex", "cursor", "gemini"])
    s.add_argument("--token", default=None, help="claude: the token from `claude setup-token` (otherwise you are asked to paste it)")
    s.add_argument("--no-check", action="store_true", help="claude: skip the one-line test turn")
    s.set_defaults(fn=cmd_login)

    s = sub.add_parser("project", help="projects: list | delete <name> [--purge] (purge also deletes the repository on the hub)")
    s.add_argument("action", choices=["list", "delete"])
    s.add_argument("name", nargs="?", default=None)
    s.add_argument("--purge", action="store_true", help="also delete the project's files on the hub; the local copy moves to ~/hiveswarm/.trash")
    s.add_argument("--yes", "-y", action="store_true")
    s.set_defaults(fn=cmd_project)

    s = sub.add_parser("sync", help="update this machine's copy of every project (or one) from the hub right now")
    s.add_argument("project", nargs="?", default=None)
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("lead", help="talk to the project's standing lead (starts it if needed)")
    s.add_argument("project")
    s.add_argument("message", nargs="?", default=None)
    s.add_argument("--permission", default="auto", choices=["auto", "acceptEdits", "bypass"])
    s.set_defaults(fn=cmd_lead)

    s = sub.add_parser("ui", help="serve the Hiveswarm web app and open it in your browser")
    s.add_argument("--port", type=int, default=7790)
    s.add_argument("--host", default="127.0.0.1", help="bind address (0.0.0.0 to reach it from other tailnet devices)")
    s.add_argument("--no-open", action="store_true")
    s.set_defaults(fn=cmd_ui)

    s = sub.add_parser("lead-skill", help="install the hiveswarm-lead skill into your own Claude Code (~/.claude/skills)")
    s.add_argument("--dir", default=None, help="Claude config dir (default: $CLAUDE_CONFIG_DIR or ~/.claude)")
    s.set_defaults(fn=cmd_lead_skill)

    s = sub.add_parser("report", help="diagnostics dump to paste when something breaks (hm report > hiveswarm-report.txt)")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("net", help="connection log: what this machine sent to the daemon, errors and outages (hm net --watch 5)")
    s.add_argument("--since", default="60m", help="window: 30m, 2h, 1d (default 60m)")
    s.add_argument("--role", default=None, help="worker, ui, or both (default)")
    s.add_argument("--watch", default=None, metavar="SECONDS", help="refresh every N seconds")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_net)

    s = sub.add_parser("classify", help="classify arbitrary text via the decide service")
    s.add_argument("text")
    s.add_argument("--no-cache", action="store_true")
    s.set_defaults(fn=cmd_classify)

    a = p.parse_args()
    if a.version:
        cmd_version(a)
        return
    if not a.cmd:
        p.print_help()
        return
    try:
        a.fn(a)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
