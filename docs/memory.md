# Memory

Off by default. Switched on, the swarm remembers what it learned: every failed attempt (what was tried, what the verifier said) and every piece of work that landed, plus anything you, the lead or the advisor tell it to keep. Before the lead plans, what matches its goal goes into its prompt; the advisor gets what matches your message; both can search with `hm_recall` and keep lessons with `hm_remember`; `hm recall` and `hm remember` do the same from a terminal.

```toml
[memory]
backend = "local"          # "none" (default) | "local" | "hindsight"

[memory.hindsight]         # only for backend = "hindsight"
url = "http://127.0.0.1:8888"
token = ""                 # if the server requires one
budget = "low"             # recall effort: low | mid | high
bank_prefix = "hiveswarm"  # banks are named <prefix>-<project> and <prefix>-global
```

`config.toml` is re-read when it changes, so switching backends takes effect on the next recall.

## Backends

- **local**: a full-text index (SQLite FTS5) inside the daemon's own database. No model, no network, nothing to run; keyword search ranked with BM25. Enough for "did we hit this before".
- **hindsight**: a [Hindsight](https://github.com/vectorize-io/hindsight) server (MIT), which extracts facts and entities from what it is given and recalls by meaning, keywords, graph links and time. One bank per scope, `hiveswarm-<project>` and `hiveswarm-global`; a bank that does not exist yet is simply empty. Hiveswarm uses `POST /v1/default/banks/<bank>/memories` to retain and `…/memories/recall` to recall; `reflect` is left to you in Hindsight's own UI.

## Scopes

Each memory belongs to a project or to `global`. Recall for a project returns its own memories and the global ones; another project never sees them. `hm_remember(text, project)` keeps a project lesson; `hm_remember(text, global_scope=True)` keeps one for every project (`hm remember "…" --global`).

## What is kept automatically

When an attempt finishes, the daemon stores one line:

- on failure: `Failed on <project>: "<spec>" by <agent> (<type>): <the verifier's last lines>`
- on success: `Done on <project>: "<spec>" by <agent> (<type>; <lines> lines changed, <seconds>s); acceptance: <command>`

Interactive sessions that succeed are not stored (that is your own work); a session that fails is. The task log shows `remembered: …` when something was kept. Deferred items have their own ledger ([craft.md](craft.md)) and are not duplicated here.

## Where it shows up

- The lead's prompt, when it starts or reopens, ends with *What the swarm learned before* for its goal.
- The advisor's system prompt carries what matches your latest message.
- `hm_recall(query, project)` from either; `GET /memory/recall?q=…&project=…` from anything else; `GET /memory` says which backend is on.
