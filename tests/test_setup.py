"""hm up planning (single machine vs. a laptop pointed at a hub), hm init's guard, hm migrate from a Hivemind setup,
and the branch-name fallback for work pushed before the rename."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(code: str, home: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HIVESWARM_", "HIVEMIND_"))}
    env.update({"HOME": str(home), "PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], env=env, capture_output=True, text=True)


def _plan(home: Path, extra_env: dict[str, str] | None = None) -> dict:
    r = _run("import json; from hiveswarm import setup_cmd; print(json.dumps(setup_cmd.plan()))", home, extra_env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_plan_hub_mode_starts_only_worker_and_app(tmp_path):
    (tmp_path / ".hiveswarm").mkdir()
    (tmp_path / ".hiveswarm" / "worker.toml").write_text('daemon_url = "http://hub.example:7778"\n[agents.codex]\nadapter = "codex"\n')
    pl = _plan(tmp_path)
    assert pl["remote"] is True
    assert pl["services"] == ["worker", "ui"]
    assert any("no HIVESWARM_TOKEN" in n for n in pl["notes"])
    (tmp_path / ".hiveswarm" / "env").write_text("HIVESWARM_TOKEN=abc\n")
    assert not any("no HIVESWARM_TOKEN" in n for n in _plan(tmp_path)["notes"])


def test_plan_single_machine_needs_a_config(tmp_path):
    (tmp_path / ".hiveswarm").mkdir()
    (tmp_path / ".hiveswarm" / "worker.toml").write_text('daemon_url = "http://127.0.0.1:7778"\n')
    assert "error" in _plan(tmp_path)
    (tmp_path / ".hiveswarm" / "config.toml").write_text("[daemon]\nport = 7778\n")
    assert _plan(tmp_path)["services"] == ["decide", "daemon", "worker", "ui"]


def test_plan_reads_a_legacy_worker_config(tmp_path):
    (tmp_path / ".config" / "hivemind").mkdir(parents=True)
    (tmp_path / ".config" / "hivemind" / "worker.toml").write_text('daemon_url = "http://spark:7778"\n')
    pl = _plan(tmp_path, {"HIVEMIND_TOKEN": "t"})
    assert pl["remote"] and pl["worker_config"].endswith(".config/hivemind/worker.toml")


def test_init_refuses_to_overwrite_a_worker(tmp_path):
    (tmp_path / ".config" / "hivemind").mkdir(parents=True)
    (tmp_path / ".config" / "hivemind" / "worker.toml").write_text('daemon_url = "http://spark:7778"\n')
    r = subprocess.run([sys.executable, "-m", "hiveswarm.cli", "init"], capture_output=True, text=True,
                       env={"HOME": str(tmp_path), "PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 2 and "hm migrate" in r.stderr
    assert not (tmp_path / ".hiveswarm" / "worker.toml").exists()


def test_migrate_copies_a_hivemind_laptop(tmp_path):
    legacy = tmp_path / ".config" / "hivemind"
    legacy.mkdir(parents=True)
    (legacy / "worker.toml").write_text(textwrap.dedent('''\
        daemon_url = "http://spark-box:7778"
        spark_ssh = "me@spark-box"
        poll_seconds = 10
        hook_port = 7791

        [agents.claude_code]
        adapter = "claude_code"
        oauth_token_file = "~/.config/hivemind/claude-token"
        concurrency = 3
        '''))
    (legacy / "claude-token").write_text("sk-ant-oat01-test\n")
    (tmp_path / "hivemind-work").mkdir()
    (tmp_path / "hivemind").mkdir()
    units = tmp_path / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    (units / "hivemind-worker.service").write_text("[Service]\nEnvironment=HIVEMIND_TOKEN=from-unit\nExecStart=%h/.local/bin/hivemind-worker\n")
    r = subprocess.run([sys.executable, "-m", "hiveswarm.cli", "migrate"], capture_output=True, text=True,
                       env={"HOME": str(tmp_path), "PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    home = tmp_path / ".hiveswarm"
    import tomllib
    w = tomllib.loads((home / "worker.toml").read_text())
    assert w["root"] == "~/hivemind-work" and w["local_projects"] == "~/hivemind", "existing mirrors and copies are reused"
    assert w["hub_ssh"] == "me@spark-box" and w["agents"]["claude_code"]["concurrency"] == 3
    env = (home / "env").read_text()
    assert "HIVESWARM_URL=http://spark-box:7778" in env and "HIVESWARM_TOKEN=from-unit" in env
    assert stat.S_IMODE((home / "env").stat().st_mode) == 0o600
    assert (home / "claude-token").read_text().startswith("sk-ant-")
    assert stat.S_IMODE((home / "claude-token").stat().st_mode) == 0o600
    assert (legacy / "worker.toml").exists(), "migrate copies; the old folder stays"
    assert "uv tool uninstall hivemind" in r.stdout
    again = subprocess.run([sys.executable, "-m", "hiveswarm.cli", "migrate"], capture_output=True, text=True,
                           env={"HOME": str(tmp_path), "PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert again.returncode == 0 and "kept" in again.stdout, "running it twice changes nothing"
    pl = _plan(tmp_path)
    assert pl["remote"] and pl["services"] == ["worker", "ui"] and pl["worker_config"].endswith(".hiveswarm/worker.toml")


def test_task_branch_prefers_new_name_and_finds_old(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*g, "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "a").write_text("a")
    subprocess.run([*g, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*g, "commit", "-qm", "a"], cwd=repo, check=True)
    subprocess.run(["git", "branch", "hivemind/old1"], cwd=repo, check=True)
    subprocess.run(["git", "branch", "hiveswarm/new1"], cwd=repo, check=True)
    code = f"""
        from hiveswarm import worktree
        print(worktree.task_branch("old1", {str(repo)!r}), worktree.task_branch("new1", {str(repo)!r}), worktree.task_branch("none", {str(repo)!r}))
    """
    r = _run(code, tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["hivemind/old1", "hiveswarm/new1", "hiveswarm/none"]
