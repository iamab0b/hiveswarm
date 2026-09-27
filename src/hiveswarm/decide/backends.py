from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ..config import env_secret, load

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


class BackendError(Exception):
    pass


def _levels(q: dict[str, Any]) -> list[str]:
    c = q.get("criteria") or []
    if isinstance(c, dict):
        return list(c.keys())
    return [x if isinstance(x, str) else str(x.get("name") or x.get("label") or x) for x in c]


def jev(model: str, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
    cfg = load()
    key = env_secret("TYPESAFE_API_KEY")
    if not key:
        raise BackendError("TYPESAFE_API_KEY not set")
    url = cfg.get("decide.jev.url", "https://api.typesafe.ai/v1/systemone")
    timeout = cfg.get("decide.timeout_seconds", 15)
    r = httpx.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "state": state, "questions": questions},
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise BackendError(f"jev {r.status_code}: {r.text[:300]}")
    body = r.json()
    answers = body.get("answers") or body
    return _normalize(answers, questions)


def local(model: str, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
    """A local OpenAI-compatible chat endpoint (llama.cpp, llama-swap, Ollama, vLLM …) answering the questions as JSON."""
    cfg = load()
    return _chat_completions(
        url=cfg.get("decide.local.url") or "http://127.0.0.1:8080/v1/chat/completions",
        model=cfg.get("decide.local.model", "coder"),
        api_key=env_secret("HIVESWARM_LOCAL_API_KEY"),
        timeout=cfg.get("decide.timeout_seconds", 15) * 4,
        state=state, questions=questions, name="local")


def openai(model: str, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
    """Any OpenAI-compatible endpoint: OpenAI, Anthropic's compatibility endpoint, OpenRouter,
    Groq, Together, a gateway. Config: decide.openai.url (full chat-completions URL or a base URL ending in /v1),
    decide.openai.model, and the key in $OPENAI_API_KEY or $HIVESWARM_DECIDE_API_KEY."""
    cfg = load()
    url = cfg.get("decide.openai.url") or "https://api.openai.com/v1/chat/completions"
    if url.rstrip("/").endswith("/v1"):
        url = url.rstrip("/") + "/chat/completions"
    return _chat_completions(
        url=url,
        model=cfg.get("decide.openai.model", "gpt-4o-mini"),
        api_key=env_secret("HIVESWARM_DECIDE_API_KEY") or env_secret("OPENAI_API_KEY"),
        timeout=cfg.get("decide.timeout_seconds", 15) * 4,
        state=state, questions=questions, name="openai")


def _chat_completions(url: str, model: str, api_key: str | None, timeout: float, state: Any,
                      questions: dict[str, Any], name: str) -> dict[str, Any]:
    lmodel = model

    schema_lines = []
    for k, q in questions.items():
        t = q["type"]
        if t == "choice":
            schema_lines.append(f'  "{k}": {{"choice": one of {json.dumps(_levels(q))}, "confidence": 0..1}}')
        elif t == "score":
            schema_lines.append(f'  "{k}": {{"score": one of {json.dumps(_levels(q))}, "confidence": 0..1}}')
        else:
            schema_lines.append(f'  "{k}": {{"noul": probability 0..1 that the statement is true}}')
    qtext = "\n".join(f"- {k} ({q['type']}): {q['instructions']}" for k, q in questions.items())
    prompt = (
        "You are a classifier. Answer ONLY with a JSON object, no prose, no code fence.\n\n"
        f"Questions:\n{qtext}\n\n"
        "Output exactly this shape:\n{\n" + ",\n".join(schema_lines) + "\n}\n\n"
        f"State:\n{state if isinstance(state, str) else json.dumps(state)}"
    )
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    r = httpx.post(
        url,
        headers=headers,
        json={
            "model": lmodel,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 400,
            "temperature": 0,
        },
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise BackendError(f"{name} {r.status_code}: {r.text[:300]}")
    text = r.json()["choices"][0]["message"]["content"]
    m = _JSON_BLOCK.search(text)
    if not m:
        raise BackendError(f"{name} backend returned no JSON")
    parsed = json.loads(m.group(0))
    return _normalize(parsed, questions)


def mock(model: str, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, q in questions.items():
        lv = _levels(q)
        if q["type"] == "choice":
            out[k] = {"choice": lv[0] if lv else "unknown", "confidence": 0.5,
                      "probabilities": {x: 1.0 / len(lv) for x in lv} if lv else {}}
        elif q["type"] == "score":
            mid = (len(lv) - 1) / 2 if lv else 0
            out[k] = {"score": mid, "level": lv[int(mid)] if lv else None, "confidence": 0.5,
                      "distribution": {x: 1.0 / len(lv) for x in lv} if lv else {}}
        else:
            text = state if isinstance(state, str) else str(state)
            hot = any(w in text.lower() for w in ("violat", "copied from", "copy it from", "force-push", "force push", "rm -rf ~"))
            out[k] = {"noul": 0.9 if hot else 0.2}
    return out


def _normalize(answers: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, q in questions.items():
        a = answers.get(k) or {}
        lv = _levels(q)
        t = q["type"]
        if t == "choice":
            choice = a.get("choice")
            probs = a.get("probabilities") or {}
            if choice is None and probs:
                choice = max(probs, key=probs.get)
            conf = a.get("confidence")
            if conf is None and probs and choice in probs:
                conf = probs[choice]
            out[k] = {"choice": choice, "confidence": float(conf or 0.0), "probabilities": probs}
        elif t == "score":
            dist = a.get("distribution") or a.get("probabilities") or {}
            score = a.get("score")
            if isinstance(score, str) and score in lv:
                score = float(lv.index(score))
            elif score is None and dist and lv:
                score = sum(dist.get(x, 0.0) * i for i, x in enumerate(lv))
            elif score is None:
                score = (len(lv) - 1) / 2 if lv else 0.0
            score = float(score)
            level = lv[min(max(int(round(score)), 0), len(lv) - 1)] if lv else None
            conf = a.get("confidence")
            if conf is None and dist and level in dist:
                conf = dist[level]
            out[k] = {"score": score, "level": level, "confidence": float(conf or 0.0), "distribution": dist}
        else:
            p = a.get("noul")
            if p is None:
                p = a.get("probability", 0.5)
            out[k] = {"noul": float(p)}
    return out


BACKENDS = {"jev": jev, "local": local, "openai": openai, "mock": mock}
