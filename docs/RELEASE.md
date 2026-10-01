# Release plan

## Names

- **PyPI**: not used. Releases are GitHub Releases with the desktop installers and the wheel attached; `hiveswarm` is free on PyPI (as of 2026-09-26) should that change.
- **Import**: `hiveswarm`. **CLI**: `hm`, plus `hiveswarm-daemon`, `hiveswarm-decide`, `hiveswarm-worker`, `hiveswarm-mcp`.
- **GitHub**: `iamab0b/hiveswarm`. **Entry-point group**: `hiveswarm.adapters`.

## Before tagging

- [ ] `pytest -q` green locally (including `-m e2e`), CI green on `main`.
- [ ] `src/hiveswarm/ui_dist/` rebuilt from `webapp/` (CI's `webapp` job checks the file list).
- [ ] `CHANGELOG.md`: move `[Unreleased]` into the new version with the date; bump `version` in `pyproject.toml`, `src/hiveswarm/__init__.py`, `webapp/package.json` and `desktop/package.json` (all the same).
- [ ] Fresh-install check: the desktop app from a `workflow_dispatch` draft on a machine without a checkout, through the first run; then `hm add smoke "…"` with a real agent and one `hm session new`.
- [ ] README screenshots current (`python tests/screenshots.py`).

## Publishing

`.github/workflows/release.yml` runs on every `v*` tag: it builds the wheel, then Hiveswarm Desktop for Windows (NSIS installer + portable), Linux (AppImage + deb) and macOS (dmg for Apple silicon and Intel, unsigned) with the wheel bundled, and creates the GitHub Release with them plus `SHA256SUMS.txt` and generated notes. If one platform fails, the release still publishes the others and its notes say which one is missing. CI's `desktop-package` job builds all three platforms on every push, so a packaging problem shows up before a tag.

```bash
git tag -a v0.2.0 -m "Hiveswarm 0.2.0" && git push origin v0.2.0
```

Then check the release page, download the installer for your own machine and go through a first run. `workflow_dispatch` produces a draft release for a dry run.

Signing: macOS builds are unsigned (users right-click → Open once); Windows builds are unsigned (SmartScreen shows "unknown publisher" until the app has reputation). Both are fine for 0.x; a signing certificate can be added to the workflow later without changing anything else.

## Released

- 0.1.0 (2026-09-26): first public release. 0.1.1 (2026-09-27): hub mode for the desktop app, `hm migrate`.
- 0.2.0 (2026-10-01): design system, live lane counts, the Craft ruleset, agent profiles and rosters, the advisor, memory.
- 0.2.1 (2026-10-01): one lane count per agent, hub lanes on the Agents page, the model picker.

## After 0.2

- 0.2.x: bug fixes from first users of the advisor and profiles; no interface changes.
- 0.3: the daemon HTTP API gets a version prefix; worker config validated with a schema and `hm doctor`; macOS worker support tested in CI; Letta Code as an adapter.
- Adapter contract and MCP tool set are frozen at 1.0.

## Announcing

The README's first screenshot and the five bullets under it are the pitch. Good venues: the Claude Code, Codex and Cursor communities (each gets a "your agent as part of a team" angle), r/LocalLLaMA for the local-model-box story, Hacker News once a few outside users have run it.
