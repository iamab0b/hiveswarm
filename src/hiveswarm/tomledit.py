"""Small, careful edits to the TOML files Hiveswarm owns (config.toml, worker.toml).

The standard library reads TOML but does not write it, and the files are the user's: comments and layout must
survive an edit. So an edit replaces or appends one `[section]` at a time as text, re-parses the result before
writing, and emits only the value kinds the config uses (strings, numbers, booleans, lists of scalars and
sub-tables)."""
from __future__ import annotations

import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any


def atomic_write(path: Path, text: str) -> None:
    """Write the whole file through a temporary file and rename, so a reader (the daemon re-reading config.toml
    on change, a worker watching worker.toml) never sees a truncated file."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        try:
            os.chmod(tmp, path.stat().st_mode & 0o777)
        except FileNotFoundError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(fmt(v) for v in value) + "]"
    raise TypeError(f"cannot write {type(value).__name__} to TOML")


def render_section(name: str, values: dict[str, Any]) -> str:
    """`[name]` with its scalar keys, followed by `[name.sub]` tables for dict values."""
    lines = [f"[{name}]"]
    subs = []
    for k, v in values.items():
        if isinstance(v, dict):
            subs.append((k, v))
        elif v is not None:
            lines.append(f"{k} = {fmt(v)}")
    out = "\n".join(lines) + "\n"
    for k, v in subs:
        out += "\n" + render_section(f"{name}.{k}", v)
    return out


def _span(text: str, section: str) -> tuple[int, int] | None:
    """Start and end of `[section]` and its sub-tables in the file text."""
    m = re.search(r"^\[" + re.escape(section) + r"\][ \t]*(#.*)?\n", text, re.M)
    if not m:
        return None
    end = len(text)
    for nxt in re.finditer(r"^\[([^\]]+)\]", text[m.end():], re.M):
        if not nxt.group(1).startswith(section + "."):
            end = m.end() + nxt.start()
            break
    return m.start(), end


def read(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def write_section(path: Path, section: str, values: dict[str, Any] | None) -> dict[str, Any]:
    """Replace `[section]` (and its sub-tables) with `values`, or remove it when values is None. Returns the parsed file."""
    text = path.read_text() if path.exists() else ""
    span = _span(text, section)
    block = render_section(section, values) if values is not None else ""
    if span:
        before = text[:span[0]].rstrip("\n")
        after = text[span[1]:].lstrip("\n")
        new = (before + ("\n\n" if before else "") + block + ("\n" if block and after else "") + after).rstrip("\n") + "\n"
    elif block:
        new = (text.rstrip("\n") + "\n\n" + block).lstrip("\n")
    else:
        return tomllib.loads(text)
    parsed = tomllib.loads(new)
    atomic_write(path, new)
    return parsed


def update_section(path: Path, section: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Set keys in `[section]` (a value of None deletes the key), keeping the keys and sub-tables already there."""
    current = read(path) if path.exists() else {}
    node: Any = current
    for part in section.split("."):
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            break
    values = dict(node) if isinstance(node, dict) else {}
    for k, v in updates.items():
        if v is None:
            values.pop(k, None)
        else:
            values[k] = v
    return write_section(path, section, values)
