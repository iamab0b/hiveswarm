from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEX = re.compile(r"\b[0-9a-f]{12,64}\b", re.I)
_TS = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?\b")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_EPOCH = re.compile(r"\b1[5-9]\d{8,12}\b")
_ABSPATH = re.compile(r"(?<![\w/])/(?:home|opt|mnt|tmp|var|Users)/[^\s'\"]+")
_WS = re.compile(r"\s+")


def canonicalize(state: Any) -> str:
    text = state if isinstance(state, str) else json.dumps(state, sort_keys=True, ensure_ascii=False)
    text = _UUID.sub("<uuid>", text)
    text = _HEX.sub("<hex>", text)
    text = _TS.sub("<ts>", text)
    text = _DATE.sub("<date>", text)
    text = _EPOCH.sub("<epoch>", text)
    text = _ABSPATH.sub("<path>", text)
    text = _WS.sub(" ", text).strip().lower()
    return text


def cache_key(model: str, questions: dict[str, Any], state: Any) -> str:
    h = hashlib.sha256()
    h.update(model.encode())
    h.update(b"\x00")
    h.update(json.dumps(questions, sort_keys=True, separators=(",", ":")).encode())
    h.update(b"\x00")
    h.update(canonicalize(state).encode())
    return h.hexdigest()
