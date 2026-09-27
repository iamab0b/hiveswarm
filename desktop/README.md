# Hiveswarm Desktop

An Electron shell that makes Hiveswarm a desktop app: it finds or installs the engine (the Python package, from
the wheel bundled in `resources/engine/`), runs `hm up`, and shows the swarm in its own window with a tray icon.
On Windows the engine runs inside WSL2 (that is where the agent CLIs live); on Linux and macOS it runs locally.

```bash
npm ci
npm run engine        # builds ../dist/hiveswarm-*.whl, which gets bundled
npm start             # run from source
npm test              # unit tests; HIVESWARM_WHEEL_DIR=../dist npm test also boots a real engine (Linux)
npm run dist:linux    # or dist:win / dist:mac → release/
```

`src/engine.js` is the engine manager (no Electron dependency, unit-tested with node:test); `src/main.js` the
window, tray and IPC; `src/boot.html` the status/settings screen shown until the app is up.

See [docs/desktop.md](../docs/desktop.md) for what users see.
