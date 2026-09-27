from __future__ import annotations

import os
from typing import Any

import httpx


class DaemonError(Exception):
    pass


class Client:
    def __init__(self, url: str | None = None, token: str | None = None):
        self.url = (url or os.environ.get("HIVESWARM_URL", "http://127.0.0.1:7778")).rstrip("/")
        tok = token or os.environ.get("HIVESWARM_TOKEN")
        self.headers = {"Authorization": f"Bearer {tok}"} if tok else {}
        self._c = httpx.AsyncClient(base_url=self.url, headers=self.headers, timeout=20,
                                    limits=httpx.Limits(keepalive_expiry=3))

    async def close(self) -> None:
        await self._c.aclose()

    async def _get(self, path: str, **params: Any) -> Any:
        q = {k: v for k, v in params.items() if v is not None}
        try:
            r = await self._c.get(path, params=q)
        except httpx.ConnectError as e:
            raise DaemonError(f"cannot reach {self.url} — retrying") from e
        except httpx.TransportError:
            try:
                r = await self._c.get(path, params=q)
            except httpx.HTTPError as e:
                raise DaemonError(f"lost connection to {self.url} ({type(e).__name__}) — retrying") from e
        if r.status_code == 401:
            raise DaemonError("unauthorized — HIVESWARM_TOKEN missing or wrong")
        if r.status_code >= 400:
            raise DaemonError(f"{r.status_code}: {r.text[:200]}")
        return r.json()

    async def _post(self, path: str, body: dict[str, Any] | None = None, timeout: float = 20) -> Any:
        try:
            r = await self._c.post(path, json=body or {}, timeout=timeout)
        except httpx.HTTPError as e:
            raise DaemonError(f"request to {self.url} failed ({type(e).__name__}); try again") from e
        if r.status_code == 401:
            raise DaemonError("unauthorized — HIVESWARM_TOKEN missing or wrong")
        if r.status_code >= 400:
            raise DaemonError(f"{r.status_code}: {r.text[:200]}")
        return r.json() if r.content else None

    async def _delete(self, path: str) -> Any:
        try:
            r = await self._c.delete(path)
        except httpx.HTTPError as e:
            raise DaemonError(f"request to {self.url} failed ({type(e).__name__}); try again") from e
        if r.status_code >= 400:
            raise DaemonError(f"{r.status_code}: {r.text[:200]}")
        return r.json() if r.content else None

    async def health(self) -> dict[str, Any]:
        return await self._get("/health")

    async def summary(self) -> dict[str, Any]:
        return await self._get("/summary")

    async def projects(self) -> dict[str, Any]:
        return await self._get("/projects")

    async def tasks(self, project: str | None = None, state: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        return await self._get("/tasks", project=project, state=state, limit=limit)

    async def task(self, tid: str) -> dict[str, Any]:
        return await self._get(f"/tasks/{tid}")

    async def add(self, project: str, spec: str, acceptance: str | None, base_ref: str = "HEAD") -> dict[str, Any]:
        return await self._post("/tasks", {"project": project, "spec": spec, "acceptance": acceptance, "base_ref": base_ref})

    async def log(self, tid: str, since: int = 0) -> dict[str, Any]:
        return await self._get(f"/tasks/{tid}/log", since=since)

    async def create_project(self, name: str, repo_path: str | None = None) -> dict[str, Any]:
        return await self._post("/projects", {"name": name, "repo_path": repo_path}, timeout=60)

    async def feed(self, since: int | None = None, tail: int | None = None) -> dict[str, Any]:
        return await self._get("/feed", since=since, tail=tail)

    async def cancel(self, tid: str) -> dict[str, Any]:
        return await self._post(f"/tasks/{tid}/cancel")

    async def retry(self, tid: str, spec: str | None = None, acceptance: str | None = None) -> dict[str, Any]:
        return await self._post(f"/tasks/{tid}/retry", {"spec": spec, "acceptance": acceptance})

    async def delete(self, tid: str) -> dict[str, Any]:
        return await self._delete(f"/tasks/{tid}")

    async def diff(self, tid: str) -> dict[str, Any]:
        return await self._get(f"/tasks/{tid}/diff")

    async def merge(self, tid: str) -> dict[str, Any]:
        return await self._post(f"/tasks/{tid}/merge", timeout=90)

    async def agents(self) -> list[dict[str, Any]]:
        return await self._get("/agents")

    async def stats(self) -> list[dict[str, Any]]:
        return await self._get("/stats")

    async def sessions(self, project: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return await self._get("/sessions", project=project, limit=limit)

    async def session(self, tid: str) -> dict[str, Any]:
        return await self._get(f"/sessions/{tid}")

    async def new_session(self, project: str, spec: str, acceptance: str | None, agent: str,
                          permission_mode: str = "auto", count: int = 1, base_ref: str = "HEAD",
                          lead: bool = False) -> dict[str, Any]:
        return await self._post("/sessions", {"project": project, "spec": spec, "acceptance": acceptance,
                                              "agent": agent, "permission_mode": permission_mode, "count": count,
                                              "base_ref": base_ref, "lead": lead, "origin": "tui"})

    async def session_send(self, tid: str, text: str | None = None, keys: list[str] | None = None) -> dict[str, Any]:
        return await self._post(f"/sessions/{tid}/send", {"text": text, "keys": keys})

    async def session_answer(self, tid: str, choice: str, text: str | None = None) -> dict[str, Any]:
        return await self._post(f"/sessions/{tid}/answer", {"choice": choice, "text": text})

    async def session_finish(self, tid: str) -> dict[str, Any]:
        return await self._post(f"/sessions/{tid}/finish")

    async def session_viewed(self, tid: str) -> dict[str, Any]:
        return await self._post(f"/sessions/{tid}/viewed")

    async def inbox(self) -> dict[str, Any]:
        return await self._get("/inbox")

    async def session_continue(self, tid: str, agent: str, permission_mode: str | None = None) -> dict[str, Any]:
        return await self._post(f"/sessions/{tid}/continue", {"agent": agent, "permission_mode": permission_mode})
