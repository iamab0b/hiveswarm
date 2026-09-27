"use strict";
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { Engine, shQuote } = require("../src/engine");

test("shell quoting survives single quotes", () => {
  assert.strictEqual(shQuote("it's"), `'it'\\''s'`);
});

test("argv goes through wsl.exe on Windows and a login shell elsewhere", () => {
  const w = new Engine({ resourcesPath: "/x", logPath: null, settings: { distro: "Ubuntu", user: "sam" }, platform: "win32" });
  const a = w.argv("hm --version");
  assert.ok(a.cmd.endsWith("wsl.exe"));
  assert.deepStrictEqual(a.args, ["-d", "Ubuntu", "-u", "sam", "--", "bash", "-lc", "hm --version"]);
  const l = new Engine({ resourcesPath: "/x", logPath: null, settings: {}, platform: "linux" });
  assert.deepStrictEqual(l.argv("hm --version"), { cmd: "bash", args: ["-lc", "hm --version"] });
  const custom = new Engine({ resourcesPath: "/x", logPath: null, settings: { shell: "zsh -lic" }, platform: "darwin" });
  assert.deepStrictEqual(custom.argv("x"), { cmd: "zsh", args: ["-lic", "x"] });
});

test("bundledWheel reads the version from the file name", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hs-res-"));
  fs.mkdirSync(path.join(dir, "engine"));
  fs.writeFileSync(path.join(dir, "engine", "hiveswarm-0.1.0-py3-none-any.whl"), "");
  const e = new Engine({ resourcesPath: dir, logPath: null, settings: {}, platform: "linux" });
  assert.deepStrictEqual(e.bundledWheel().version, "0.1.0");
  assert.strictEqual(new Engine({ resourcesPath: "/nope", logPath: null, settings: {} }).bundledWheel(), null);
});

test("health reports false when nothing listens", async () => {
  const e = new Engine({ resourcesPath: "/x", logPath: null, settings: { port: 9 }, platform: "linux" });
  assert.deepStrictEqual(await e.health(), { ui: false, daemon: false });
});

// Full boot against a real wheel: installs into a scratch HOME, runs hm init and hm up, then stops. Linux only,
// needs python3 and network for pip; skipped unless HIVESWARM_WHEEL_DIR points at a directory with the wheel.
test("installs the engine from the wheel, initialises and starts the app", { skip: process.platform !== "linux" || !process.env.HIVESWARM_WHEEL_DIR, timeout: 15 * 60 * 1000 }, async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "hs-home-"));
  const res = fs.mkdtempSync(path.join(os.tmpdir(), "hs-res-"));
  fs.mkdirSync(path.join(res, "engine"));
  for (const f of fs.readdirSync(process.env.HIVESWARM_WHEEL_DIR)) {
    if (f.endsWith(".whl")) fs.copyFileSync(path.join(process.env.HIVESWARM_WHEEL_DIR, f), path.join(res, "engine", f));
  }
  process.env.HOME = home;
  delete process.env.HIVESWARM_HOME;
  delete process.env.HIVESWARM_CONFIG;
  delete process.env.HIVESWARM_URL;
  delete process.env.HIVESWARM_TOKEN;
  const port = 17000 + Math.floor(Math.random() * 1000);
  const e = new Engine({ resourcesPath: res, logPath: path.join(home, "engine.log"), settings: { port }, platform: "linux" });
  e.on("log", (l) => process.stdout.write("  | " + l + "\n"));
  const ok = await e.start();
  assert.strictEqual(ok, true, "engine started");
  assert.ok(fs.existsSync(path.join(home, ".hiveswarm", "venv", "bin", "hm")));
  assert.ok(fs.existsSync(path.join(home, ".hiveswarm", "config.toml")));
  assert.ok(fs.existsSync(path.join(home, ".local", "bin", "hm")));
  const h = await e.health();
  assert.ok(h.ui, "app answers");
  await e.stop();
  assert.strictEqual(e.proc, null);
  assert.strictEqual(e.state, "stopped");
});
