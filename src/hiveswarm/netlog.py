"""Connection log: every request a Hiveswarm process makes to the daemon, with timing, size and outcome.

Why: a laptop worker talks to the hub over Wi-Fi, often through a VPN and WSL2's NAT. When that link misbehaves
the first question is "what was Hiveswarm doing on the network at the time?". This module answers it without
any external tooling: each process appends one JSON line per request to ~/.hiveswarm/logs/net-<role>.jsonl,
tracks outages (a run of failed requests) and warns about them in its own log, and `hm net` turns the files into
a per-minute traffic picture, a per-endpoint table and an error timeline. `hm report` includes the summary.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from .config import home

log = logging.getLogger("hiveswarm.net")

MAX_BYTES = 5_000_000          # rotate the JSONL file past this size (one generation kept as .1)
OUTAGE_AFTER = 3               # consecutive failures that count as an outage


def default_path(role: str) -> Path:
    return home() / "logs" / f"net-{role}.jsonl"


def _enabled() -> bool:
    return os.environ.get("HIVESWARM_NETLOG", "1").lower() not in ("0", "off", "false", "no")


class NetLog:
    """Thread-safe append-only recorder with outage detection."""

    def __init__(self, role: str, path: Path | None = None, max_bytes: int = MAX_BYTES):
        self.role = role
        self.path = path or default_path(role)
        self.max_bytes = max_bytes
        self.enabled = _enabled()
        self._lock = threading.Lock()
        self._fails = 0
        self._outage_since: float | None = None
        self._outage_fails = 0
        self._last_err = ""
        self.count = 0
        self.errors = 0
        if self.enabled:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                self.enabled = False

    def record(self, method: str, path: str, status: int | None, ms: float, err: str | None = None,
               sent: int = 0, received: int = 0) -> None:
        ok = err is None and status is not None and status < 500
        ev = {"ts": round(time.time(), 3), "role": self.role, "m": method, "p": _bucket(path), "s": status,
              "ms": round(ms, 1), "tx": int(sent or 0), "rx": int(received or 0)}
        if err:
            ev["err"] = err[:200]
        with self._lock:
            self.count += 1
            if not ok:
                self.errors += 1
            self._track(ok, err or (f"HTTP {status}" if status else "?"))
            if self.enabled:
                self._append(ev)

    def _track(self, ok: bool, err: str) -> None:
        now = time.time()
        if ok:
            if self._outage_since is not None:
                secs = now - self._outage_since
                log.warning("daemon reachable again after %.0f s (%d failed requests; last error: %s)",
                            secs, self._outage_fails, self._last_err)
                self._append({"ts": round(now, 3), "role": self.role, "outage_end": round(secs, 1), "fails": self._outage_fails})
                self._outage_since = None
                self._outage_fails = 0
            self._fails = 0
            return
        self._fails += 1
        self._last_err = err
        if self._outage_since is None and self._fails >= OUTAGE_AFTER:
            self._outage_since = now
            self._outage_fails = self._fails
            log.warning("daemon unreachable: %d consecutive failed requests (%s); watching for recovery", self._fails, err)
            self._append({"ts": round(now, 3), "role": self.role, "outage_start": True, "err": err[:200]})
        elif self._outage_since is not None:
            self._outage_fails += 1

    def _append(self, ev: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                self.path.replace(self.path.with_suffix(".jsonl.1"))
            with open(self.path, "a") as f:
                f.write(json.dumps(ev, separators=(",", ":")) + "\n")
        except Exception as e:  # never let bookkeeping break the worker
            log.debug("netlog write failed: %s", e)
            self.enabled = False


def _bucket(path: str) -> str:
    """Collapse ids so endpoints group: /tasks/1a2b…/log → /tasks/{id}/log."""
    parts = path.split("/")
    out = []
    for p in parts:
        if len(p) >= 12 and all(c in "0123456789abcdef" for c in p):
            out.append("{id}")
        else:
            out.append(p)
    return "/".join(out)


class LoggedClient(httpx.Client):
    """httpx.Client that records every request (including connection errors) in a NetLog."""

    def __init__(self, netlog: NetLog, **kw: Any):
        super().__init__(**kw)
        self.netlog = netlog

    def send(self, request: httpx.Request, **kw: Any) -> httpx.Response:  # type: ignore[override]
        t0 = time.monotonic()
        try:
            r = super().send(request, **kw)
        except Exception as e:
            self.netlog.record(request.method, request.url.path, None, (time.monotonic() - t0) * 1000,
                               err=f"{type(e).__name__}: {str(e)[:120]}", sent=len(request.content or b""))
            raise
        self.netlog.record(request.method, request.url.path, r.status_code, (time.monotonic() - t0) * 1000,
                           sent=len(request.content or b""), received=int(r.headers.get("content-length") or 0))
        return r


class LoggedAsyncClient(httpx.AsyncClient):
    def __init__(self, netlog: NetLog, **kw: Any):
        super().__init__(**kw)
        self.netlog = netlog

    async def send(self, request: httpx.Request, **kw: Any) -> httpx.Response:  # type: ignore[override]
        t0 = time.monotonic()
        try:
            r = await super().send(request, **kw)
        except Exception as e:
            self.netlog.record(request.method, request.url.path, None, (time.monotonic() - t0) * 1000,
                               err=f"{type(e).__name__}: {str(e)[:120]}", sent=len(request.content or b""))
            raise
        self.netlog.record(request.method, request.url.path, r.status_code, (time.monotonic() - t0) * 1000,
                           sent=len(request.content or b""), received=int(r.headers.get("content-length") or 0))
        return r


# ── reading it back (`hm net`) ─────────────────────────────────────────────

def read_events(roles: list[str] | None = None, since: float | None = None, logs_dir: Path | None = None) -> list[dict[str, Any]]:
    d = logs_dir or (home() / "logs")
    files: list[Path] = []
    for p in sorted(d.glob("net-*.jsonl*")) if d.is_dir() else []:
        role = p.name[len("net-"):].split(".")[0]
        if roles and role not in roles:
            continue
        files.append(p)
    out: list[dict[str, Any]] = []
    for p in files:
        try:
            with open(p) as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if since is None or float(ev.get("ts", 0)) >= since:
                        out.append(ev)
        except Exception:
            continue
    out.sort(key=lambda e: e.get("ts", 0))
    return out


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    vs = sorted(values)
    i = min(len(vs) - 1, max(0, int(round(q * (len(vs) - 1)))))
    return vs[i]


def summarize(events: list[dict[str, Any]], window_s: float, now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    start = now - window_s
    reqs = [e for e in events if "m" in e and e.get("ts", 0) >= start]
    per_ep: dict[tuple[str, str, str], dict[str, Any]] = {}
    for e in reqs:
        key = (e.get("role", "?"), e["m"], e["p"])
        c = per_ep.setdefault(key, {"n": 0, "err": 0, "ms": [], "tx": 0, "rx": 0})
        c["n"] += 1
        if e.get("err") or (e.get("s") or 0) >= 500:
            c["err"] += 1
        c["ms"].append(float(e.get("ms", 0)))
        c["tx"] += int(e.get("tx", 0))
        c["rx"] += int(e.get("rx", 0))
    endpoints = []
    minutes = max(window_s / 60.0, 1e-9)
    for (role, m, p), c in sorted(per_ep.items(), key=lambda kv: -kv[1]["n"]):
        endpoints.append({"role": role, "method": m, "path": p, "n": c["n"], "per_min": round(c["n"] / minutes, 2),
                          "errors": c["err"], "p50_ms": round(_pct(c["ms"], 0.5)), "p95_ms": round(_pct(c["ms"], 0.95)),
                          "tx_kb": round(c["tx"] / 1024, 1), "rx_kb": round(c["rx"] / 1024, 1)})
    buckets: Counter[int] = Counter()
    err_buckets: Counter[int] = Counter()
    for e in reqs:
        b = int((e["ts"] - start) // 60)
        buckets[b] += 1
        if e.get("err") or (e.get("s") or 0) >= 500:
            err_buckets[b] += 1
    n_b = int(window_s // 60) or 1
    timeline = [{"minute": i, "n": buckets.get(i, 0), "errors": err_buckets.get(i, 0)} for i in range(n_b)]
    errors = [e for e in reqs if e.get("err") or (e.get("s") or 0) >= 500][-30:]
    outages: list[dict[str, Any]] = []
    open_since: dict[str, float] = {}
    for e in events:
        if e.get("ts", 0) < start - 3600:
            continue
        role = e.get("role", "?")
        if e.get("outage_start"):
            open_since[role] = e["ts"]
        elif "outage_end" in e:
            st = open_since.pop(role, e["ts"] - float(e["outage_end"]))
            if e["ts"] >= start:
                outages.append({"role": role, "start": st, "end": e["ts"], "seconds": round(e["ts"] - st), "fails": e.get("fails", 0)})
    for role, st in open_since.items():
        outages.append({"role": role, "start": st, "end": None, "seconds": round(now - st), "fails": None})
    total = len(reqs)
    return {"window_s": window_s, "requests": total, "per_min": round(total / minutes, 2),
            "errors": sum(1 for e in reqs if e.get("err") or (e.get("s") or 0) >= 500),
            "tx_kb": round(sum(int(e.get("tx", 0)) for e in reqs) / 1024, 1),
            "rx_kb": round(sum(int(e.get("rx", 0)) for e in reqs) / 1024, 1),
            "endpoints": endpoints, "timeline": timeline, "recent_errors": errors, "outages": outages,
            "roles": sorted({e.get("role", "?") for e in reqs}), "logs_dir": str(home() / "logs")}


_BARS = " ▁▂▃▄▅▆▇█"


def render(summary: dict[str, Any], now: float | None = None) -> str:
    now = now or time.time()
    lines: list[str] = []
    w = int(summary["window_s"])
    span = f"{w // 3600}h" if w >= 3600 else f"{w // 60}m"
    lines.append(f"network · last {span} · {summary['requests']} requests ({summary['per_min']}/min) · "
                 f"{summary['errors']} errors · {summary['tx_kb']} KB out / {summary['rx_kb']} KB in · roles: {', '.join(summary['roles']) or '-'}")
    tl = summary["timeline"]
    if tl:
        peak = max(b["n"] for b in tl) or 1
        spark = "".join(_BARS[min(8, int(round(8 * b["n"] / peak)))] if b["n"] else " " for b in tl)
        errs = "".join("!" if b["errors"] else " " for b in tl)
        lines.append(f"requests/min  {spark}  peak {peak}/min")
        if any(b["errors"] for b in tl):
            lines.append(f"errors        {errs}")
    if summary["outages"]:
        lines.append("outages:")
        for o in summary["outages"]:
            end = time.strftime("%H:%M:%S", time.localtime(o["end"])) if o["end"] else "still down"
            lines.append(f"  {o['role']:7} {time.strftime('%H:%M:%S', time.localtime(o['start']))} → {end}  ({o['seconds']} s"
                         + (f", {o['fails']} failed requests" if o["fails"] is not None else "") + ")")
    if summary["endpoints"]:
        lines.append(f"{'role':7} {'method':6} {'endpoint':34} {'n':>6} {'/min':>7} {'err':>4} {'p50ms':>6} {'p95ms':>6} {'out KB':>7} {'in KB':>7}")
        for e in summary["endpoints"][:25]:
            lines.append(f"{e['role']:7} {e['method']:6} {e['path'][:34]:34} {e['n']:6} {e['per_min']:7.2f} {e['errors']:4} "
                         f"{e['p50_ms']:6} {e['p95_ms']:6} {e['tx_kb']:7.1f} {e['rx_kb']:7.1f}")
    if summary["recent_errors"]:
        lines.append("recent errors:")
        for e in summary["recent_errors"][-12:]:
            lines.append(f"  {time.strftime('%H:%M:%S', time.localtime(e['ts']))} {e.get('role', '?'):7} {e['m']} {e['p']}: "
                         + (e.get("err") or f"HTTP {e.get('s')}"))
    lines.append(f"log files: {summary['logs_dir']}/net-*.jsonl")
    return "\n".join(lines)
