# Release plan: v0.1

## Names

- **PyPI**: `hiveswarm` — free as of 2026-09-26 (`hivemind`, the project's old name, is taken by an unrelated package).
- **Import**: `hiveswarm`. **CLI**: `hm`, plus `hiveswarm-daemon`, `hiveswarm-decide`, `hiveswarm-worker`, `hiveswarm-mcp`.
- **GitHub**: `iamab0b/hiveswarm`. **Entry-point group**: `hiveswarm.adapters`.

## Before tagging

- [ ] `pytest -q` green locally (including `-m e2e`), CI green on `main`.
- [ ] `src/hiveswarm/ui_dist/` rebuilt from `webapp/` (CI's `webapp` job checks the file list).
- [ ] `CHANGELOG.md`: move `[Unreleased]` into `[0.1.0]` with the date; bump `version` in `pyproject.toml`, `src/hiveswarm/__init__.py` and `webapp/package.json` (all `0.1.0`).
- [ ] Fresh-install check on a machine without a checkout:
  ```bash
  python -m build
  python -m venv /tmp/v && /tmp/v/bin/pip install dist/hiveswarm-0.1.0-py3-none-any.whl
  HIVESWARM_HOME=/tmp/hs /tmp/v/bin/hm init --project smoke && HIVESWARM_HOME=/tmp/hs /tmp/v/bin/hm up --no-open
  ```
  then `hm add smoke "…"` from another terminal with a real agent, and one `hm session new`.
- [ ] README screenshots current (`python tests/screenshots.py`).

## Publishing

1. **PyPI trusted publishing** (no API tokens in secrets): on pypi.org create the project `hiveswarm` → Publishing → add a GitHub publisher with owner `iamab0b`, repo `hiveswarm`, workflow `release.yml`, environment `pypi`. Then add `.github/workflows/release.yml`:
   ```yaml
   name: Release
   on:
     push:
       tags: ["v*"]
   jobs:
     build:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with: { python-version: "3.12" }
         - run: pip install build && python -m build
         - uses: actions/upload-artifact@v4
           with: { name: dist, path: dist/ }
     publish:
       needs: build
       runs-on: ubuntu-latest
       environment: pypi
       permissions: { id-token: write, contents: write }
       steps:
         - uses: actions/download-artifact@v4
           with: { name: dist, path: dist/ }
         - uses: pypa/gh-action-pypi-publish@release/v1
         - uses: softprops/action-gh-release@v2
           with: { files: dist/*, generate_release_notes: true }
   ```
2. Tag and push: `git tag -a v0.1.0 -m "Hiveswarm 0.1.0" && git push origin v0.1.0`. The workflow builds, publishes to PyPI and creates the GitHub release with the wheel and sdist attached.
3. Verify: `uv tool install hiveswarm && hm --version` on a clean machine.

A first manual publish works too: `pip install twine && twine upload dist/*` with a PyPI API token.

## After 0.1

- 0.1.x: bug fixes from first users; no interface changes.
- 0.2: the daemon HTTP API gets a version prefix; worker config validated with a schema and `hm doctor`; macOS worker support tested in CI.
- Adapter contract and MCP tool set are frozen at 1.0.

## Announcing

The README's first screenshot and the five bullets under it are the pitch. Good venues: the Claude Code, Codex and Cursor communities (each gets a "your agent as part of a team" angle), r/LocalLLaMA for the local-model-box story, Hacker News once a few outside users have run it.
