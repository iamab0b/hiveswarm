"""Third-party adapters: the example plugin is installed into a scratch site-packages, a fake `example-agent`
binary is put on PATH, a second worker serves it, and a task routed to it runs end to end."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

FAKE_AGENT = textwrap.dedent('''\
    #!/usr/bin/env python3
    import sys, time
    prompt = sys.argv[-1]
    print("> Read add.py"); time.sleep(0.1)
    print("< def add(a, b): ...")
    print("> Edit add.py"); time.sleep(0.1)
    with open("add.py") as f:
        src = f.read()
    doc = chr(34) * 3
    if "def add(a, b):" in src and doc not in src:
        src = src.replace("def add(a, b):", "def add(a, b):\\n    " + doc + "Return a + b (written by example-agent)." + doc)
    else:
        src += "\\n# example-agent was here\\n"
    with open("add.py", "w") as f:
        f.write(src)
    print("< edited add.py")
    print("Done.")
''')


@pytest.fixture(scope="module")
def plugin_site(tmp_path_factory):
    site = tmp_path_factory.mktemp("site")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--target", str(site),
                        str(ROOT / "examples" / "adapter-plugin")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return site


def test_plugin_is_discovered(plugin_site):
    code = textwrap.dedent("""
        from hiveswarm.workers import adapters
        added = adapters.load_plugins()
        print(added, adapters.BINARIES.get("example"), adapters.CAPABILITIES.get("example"))
    """)
    env = {**os.environ, "PYTHONPATH": f"{plugin_site}{os.pathsep}{ROOT / 'src'}"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "['example'] example-agent ['tools', 'agent_loop']" in out.stdout


def test_task_runs_on_a_plugin_agent(stack, plugin_site, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    agent = bin_dir / "example-agent"
    agent.write_text(FAKE_AGENT)
    agent.chmod(agent.stat().st_mode | stat.S_IEXEC)
    wcfg = tmp_path / "worker.toml"
    wcfg.write_text(textwrap.dedent(f"""
        daemon_url = "http://127.0.0.1:{stack.port}"
        root = "{tmp_path / 'work'}"
        poll_seconds = 1
        hook_port = 0

        [agents.example]
        adapter = "example"
        concurrency = 1
        timeout = 120

        [agents.bogus]
        adapter = "does_not_exist"
    """))
    env = {**stack.env, "PATH": f"{bin_dir}{os.pathsep}{stack.env['PATH']}",
           "PYTHONPATH": f"{plugin_site}{os.pathsep}{stack.env['PYTHONPATH']}"}
    log = open(tmp_path / "worker2.log", "ab")
    p = subprocess.Popen([sys.executable, "-m", "hiveswarm.workers.remote", "--config", str(wcfg)], env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        stack.wait_for(lambda: any(a["agent_id"] == "example" and a["alive"] for a in stack.get("/agents")), 30, "the plugin agent to register")
        text = (tmp_path / "worker2.log").read_text()
        assert "adapter plugins: example" in text
        assert "unknown adapter 'does_not_exist'" in text
        r = stack.post("/tasks", {"project": "demo", "spec": "Add a docstring to add()", "agent": "example",
                                  "acceptance": "python3 -c 'import add; assert add.add.__doc__'", "origin": "test"})
        tid = r["id"]
        stack.wait_for(lambda: stack.state(tid) in ("done", "failed"), 120, "the plugin task to finish")
        d = stack.task(tid)
        assert d["task"]["state"] == "done", stack.log_text(tid)[-2000:]
        assert d["attempts"][-1]["agent"] == "example", stack.log_text(tid)[-3000:]
        assert d["attempts"][-1]["steps"] and d["attempts"][-1]["steps"] >= 2
        assert "Edit add.py" in stack.log_text(tid)
        p.terminate()
        p.wait(timeout=10)
        stack.wait_for(lambda: not any(a["agent_id"] == "example" and a["alive"] for a in stack.get("/agents")), 10,
                       "the plugin agent to retire when its worker stops")
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
        time.sleep(0.5)
