from __future__ import annotations

import logging
import threading
import traceback
from typing import Any

from .. import db, perf, sessions, worktree
from ..classify import classify_task, diff_band
from ..config import load
from ..router import roundrobin as router
from ..verify import runner as verifier
from ..workers import local_direct, prime_agent

log = logging.getLogger("hiveswarm.dispatcher")

LOCAL_WORKERS = {prime_agent.AGENT: prime_agent, local_direct.AGENT: local_direct}


def kill_running(tid: str) -> bool:
    killed = False
    for w in LOCAL_WORKERS.values():
        k = getattr(w, "kill", None)
        if k and k(tid):
            killed = True
    return killed


def _classify_pending() -> None:
    for t in db.task_list(state="pending", limit=20):
        db.task_set_state(t["id"], "classifying")
        try:
            c = classify_task(t["id"])
            log.info("classified %s as %s d=%.1f conf=%.2f src=%s",
                     t["id"], c["task_type"], c["difficulty"], c["type_conf"], c["source"])
            db.log_append(t["id"], "daemon",
                          f"classified as {c['task_type']} (conf {c['type_conf']:.2f}), "
                          f"difficulty {c['difficulty']:.1f}, via {c['source']}")
            db.task_set_state(t["id"], "classified")
        except Exception as e:
            log.error("classify %s failed: %s", t["id"], e)
            db.task_set_state(t["id"], "pending")


def handoff_context(tid: str) -> str:
    prior = [a for a in db.attempts_for(tid) if a["outcome"]]
    if not prior:
        return ""
    lines = ["## Previous attempts on this task (do not repeat these mistakes)"]
    for a in prior[-2:]:
        lines.append(f"- {a['agent']} -> {a['outcome']}")
        if a["verifier_log"]:
            tail = a["verifier_log"].strip().splitlines()[-8:]
            lines.append("  " + "\n  ".join(tail))
    return "\n".join(lines) + "\n\n"


def recover_after_restart() -> None:
    ids = db.requeue_orphans(list(LOCAL_WORKERS.keys()))
    for tid in ids:
        db.log_append(tid, "daemon", "requeued: the daemon restarted while a hub-local agent was working on this")
        log.warning("requeued orphaned local task %s after restart", tid)
    if ids and prime_agent.available():
        try:
            prime_agent.kill_all()
        except Exception as e:
            log.warning("could not clear orphaned prime-agent processes: %s", e)


def _route_session(task: Any, lease: int) -> bool:
    s = sessions.get(task["id"]) or {}
    agent = s.get("agent")
    if not agent:
        return False
    if agent in LOCAL_WORKERS:
        db.log_append(task["id"], "daemon", f"{agent} does not support interactive sessions yet; cancel this and use a CLI agent on a worker")
        db.task_set_state(task["id"], "failed", claimed_by=None, lease_expires=None)
        return True
    alive = {a["agent_id"] for a in db.agents_alive()}
    if agent not in alive:
        return False
    busy = db.busy_counts().get(agent, 0)
    if busy >= db.agent_capacities().get(agent, 1):
        return False
    db.task_assign(task["id"], agent, lease)
    db.log_append(task["id"], "daemon", f"routed to {agent} (interactive session)")
    return True


def _route_and_dispatch(task: Any) -> bool:
    cfg = load()
    lease = int(cfg.get("daemon.lease_seconds", 1800))
    if task["kind"] == "session":
        return _route_session(task, lease)
    cls_row = db.classification_get(task["id"])
    cls = dict(cls_row) if cls_row else None
    agent = router.choose(task, cls, db.busy_agents())
    if agent is None:
        return False
    db.task_assign(task["id"], agent, lease)
    reason = router.REASONS.pop(task["id"], None)
    db.log_append(task["id"], "daemon", f"routed to {agent}" + (f" ({reason})" if reason else ""))

    if agent in LOCAL_WORKERS:
        db.task_set_state(task["id"], "claimed")
        fresh = db.task_get(task["id"])
        threading.Thread(target=_run_local, args=(fresh, cls, agent), daemon=True,
                         name=f"{agent}-{task['id'][:8]}").start()
        return True

    log.info("task %s assigned to %s", task["id"], agent)
    return True


def _run_local(task: Any, cls: dict[str, Any] | None, agent: str) -> None:
    cfg = load()
    worker = LOCAL_WORKERS[agent]
    aid = db.attempt_start(task["id"], agent, cfg.get("inference.model"))
    db.task_set_state(task["id"], "running")
    try:
        wt = worktree.create(task["id"], task["repo_path"], task["base_ref"])
        db.task_set_state(task["id"], "running", worktree=wt)
        handoff = handoff_context(task["id"])
        if handoff:
            db.log_append(task["id"], "daemon", f"handoff to {agent}:\n{handoff.strip()}")
        spec_with_context = handoff + task["spec"]
        res = worker.run({**dict(task), "spec": spec_with_context}, wt)
        cur = db.task_get(task["id"])
        if cur is None or cur["state"] == "abandoned":
            log.info("task %s cancelled mid-run; discarding worker result", task["id"])
            return
        if not res.get("ok"):
            db.log_append(task["id"], "daemon", "worker error: " + (res.get("error") or "")[:300].replace("\n", " | "))
            db.attempt_finish(aid, "error", verifier_log=res.get("error"),
                              tokens_in=res.get("tokens_in"), tokens_out=res.get("tokens_out"))
            finalize(task["id"], cls, agent, passed=False, weight=1.0)
            return
        verify_and_finalize(task["id"], aid, wt, cls, agent, res)
    except Exception as e:
        log.error("task %s crashed: %s\n%s", task["id"], e, traceback.format_exc())
        db.attempt_finish(aid, "error", verifier_log=str(e)[:2000])
        finalize(task["id"], cls, agent, passed=False, weight=1.0)


def verify_and_finalize(tid: str, aid: str, wt: str, cls: dict[str, Any] | None, agent: str, res: dict[str, Any]) -> None:
    task = db.task_get(tid)
    if task is None or task["state"] == "abandoned":
        return
    db.task_set_state(tid, "verifying")
    db.log_append(tid, "daemon", f"verifying: {task['acceptance'] or '(no acceptance command)'}")
    v = verifier.run(task, wt)
    outcome = v["outcome"]
    tail = (v.get("log") or "").strip().splitlines()[-15:]
    for line in tail:
        db.log_append(tid, "verifier", line)
    db.log_append(tid, "daemon", f"verifier result: {outcome}")
    passed = outcome in ("pass", "soft")
    db.attempt_finish(aid, "pass" if outcome == "soft" else outcome,
                      verifier_log=v.get("log"), diff_stat=res.get("diff_stat"),
                      tokens_in=res.get("tokens_in"), tokens_out=res.get("tokens_out"),
                      branch=res.get("branch"))
    finalize(tid, cls, agent, passed=passed, weight=v.get("weight", 1.0))


def finalize(tid: str, cls: dict[str, Any] | None, agent: str, passed: bool, weight: float) -> None:
    fresh = db.task_get(tid)
    if fresh is None or fresh["state"] == "abandoned":
        log.info("task %s was cancelled; not finalizing", tid)
        return
    att = db.attempt_latest(tid)
    if cls and weight > 0:
        w = weight * (0.5 if cls.get("source") == "fallback" else 1.0)
        db.stats_update(agent, cls["task_type"], diff_band(cls["difficulty"]), passed, w,
                        None, None, att["wall_seconds"] if att else None)
    if passed:
        db.task_set_state(tid, "done", claimed_by=None, lease_expires=None)
        db.log_append(tid, "daemon", f"done — branch hiveswarm/{tid} ready to merge")
        log.info("task %s done by %s", tid, agent)
        return
    if fresh["attempts"] >= fresh["max_attempts"]:
        db.task_set_state(tid, "failed", claimed_by=None, lease_expires=None)
        db.log_append(tid, "daemon", f"failed after {fresh['attempts']} attempts")
        log.info("task %s failed after %d attempts", tid, fresh["attempts"])
    else:
        if fresh["worktree"]:
            worktree.remove(tid, fresh["repo_path"])
        db.task_set_state(tid, "classified", claimed_by=None, lease_expires=None, worktree=None)
        db.log_append(tid, "daemon", f"requeued ({fresh['attempts']}/{fresh['max_attempts']}) with handoff")
        log.info("task %s requeued (%d/%d)", tid, fresh["attempts"], fresh["max_attempts"])


_last_stall_check = 0.0


def _watch_stalls() -> None:
    global _last_stall_check
    import time as _time
    if _time.monotonic() - _last_stall_check < 15:
        return
    _last_stall_check = _time.monotonic()
    try:
        perf.check_stalls()
    except Exception as e:
        log.warning("stall watch failed: %s", e)


def loop(stop: threading.Event) -> None:
    cfg = load()
    poll = int(cfg.get("daemon.poll_seconds", 5))
    log.info("dispatcher started (poll=%ss)", poll)
    while not stop.is_set():
        try:
            n = db.task_expire_leases()
            if n:
                log.warning("expired %d stale leases", n)
            for tid in db.task_expire_unclaimed(int(cfg.get("daemon.claim_seconds", 60))):
                db.log_append(tid, "daemon", "requeued: the assigned worker never picked this up")
                log.warning("task %s was never claimed; requeued", tid)
            _classify_pending()
            _watch_stalls()
            routed = False
            for task in db.task_classified_queue():
                if _route_and_dispatch(task):
                    routed = True
                    break
            if routed:
                continue
        except Exception as e:
            log.error("dispatcher iteration failed: %s\n%s", e, traceback.format_exc())
        stop.wait(poll)
    log.info("dispatcher stopped")
