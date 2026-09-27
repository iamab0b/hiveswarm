"use strict";
/**
 * The engine manager: finds or installs the Hiveswarm engine (the Python package) and runs `hm up` for the app.
 *
 * On Windows the engine lives in WSL2 (that is where the agent CLIs are), so every command goes through
 * `wsl.exe [-d distro] [-u user] -e bash -lc '<script>'` (-e passes the script to bash untouched, with no second
 * shell in between). On Linux and macOS it runs in a login shell directly.
 *
 * First run: the engine is installed into ~/.hiveswarm/venv from the wheel bundled with the app (with uv when it is
 * there or Python 3.11+ is not, else python3 -m venv), and hm plus the other entry points are linked into
 * ~/.local/bin. A machine that already has a pre-rename Hivemind setup is migrated with `hm migrate` (copies only);
 * a machine with nothing gets `hm init`. `hm up` then decides what this machine runs: everything on a single
 * machine, or only the worker and the app when worker.toml points at a remote hub.
 *
 * This module has no Electron dependency so it can be unit-tested with plain node.
 */
const { spawn, spawnSync } = require("node:child_process");
const http = require("node:http");
const path = require("node:path");
const fs = require("node:fs");
const { EventEmitter } = require("node:events");

const DEFAULTS = {
  distro: "",          // WSL distro name; "" = the default distro
  user: "",            // WSL user; "" = the distro's default user
  port: 7790,          // the app's port on 127.0.0.1
  keepRunning: false,  // leave the engine running when the app quits
  openAtLogin: false,
  shell: "",           // override the shell command, e.g. "bash -lic"
};

// Every engine command starts with this: the engine's venv and the usual places CLIs get installed to, plus nvm.
const HM_PATH = 'export PATH="$HOME/.hiveswarm/venv/bin:$HOME/.local/bin:$HOME/.bun/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"; '
  + '[ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1; true';

const MARK = "__HIVESWARM_PATH__";

function shQuote(s) {
  return "'" + String(s).replace(/'/g, `'\\''`) + "'";
}

/** Keep Linux directories only: WSL appends the whole Windows PATH under /mnt/c, which is slow to search. */
function cleanPath(p, platform) {
  const parts = String(p || "").split(":").filter(Boolean);
  const kept = platform === "win32" ? parts.filter((d) => !d.startsWith("/mnt/")) : parts;
  return [...new Set(kept)].join(":");
}

class Engine extends EventEmitter {
  /**
   * @param {object} opts
   * @param {string} opts.resourcesPath   where the bundled wheel lives (<resources>/engine/*.whl)
   * @param {string} opts.logPath         engine.log on the host machine
   * @param {object} opts.settings        see DEFAULTS
   * @param {string} [opts.platform]      override process.platform (tests)
   */
  constructor(opts) {
    super();
    this.platform = opts.platform || process.platform;
    this.resourcesPath = opts.resourcesPath;
    this.logPath = opts.logPath;
    this.settings = { ...DEFAULTS, ...(opts.settings || {}) };
    this.state = "stopped"; // stopped | checking | installing | migrating | initialising | starting | running | failed
    this.detail = "";
    this.proc = null;
    this.lines = [];
    this.userPath = "";
    this._stopping = false;
    this.wslPath = this.platform === "win32" ? path.join(process.env.SystemRoot || "C:\\Windows", "System32", "wsl.exe") : null;
  }

  // ── status ─────────────────────────────────────────────────────────────

  setState(state, detail) {
    this.state = state;
    this.detail = detail || "";
    this.emit("state", { state, detail: this.detail });
  }

  log(line) {
    const text = String(line).replace(/\s+$/, "");
    if (!text) return;
    if (/cannot set terminal process group|no job control in this shell/.test(text)) return;
    this.lines.push(text);
    if (this.lines.length > 400) this.lines.splice(0, this.lines.length - 400);
    this.emit("log", text);
    if (this.logPath) {
      try {
        fs.mkdirSync(path.dirname(this.logPath), { recursive: true });
        fs.appendFileSync(this.logPath, `${new Date().toISOString()} ${text}\n`);
      } catch {
        /* logging must never break the app */
      }
    }
  }

  get url() {
    return `http://127.0.0.1:${this.settings.port}`;
  }

  /** Is the app server answering on the port, and which one is it? Resolves {ui, daemon, version}. */
  health() {
    const get = (p) =>
      new Promise((resolve) => {
        const req = http.get({ host: "127.0.0.1", port: this.settings.port, path: p, timeout: 1500 }, (res) => {
          let body = "";
          res.setEncoding("utf8");
          res.on("data", (c) => {
            if (body.length < 8192) body += c;
          });
          res.on("end", () => resolve({ ok: res.statusCode === 200, body }));
        });
        req.on("error", () => resolve({ ok: false, body: "" }));
        req.on("timeout", () => {
          req.destroy();
          resolve({ ok: false, body: "" });
        });
      });
    return Promise.all([get("/api/local"), get("/api/summary")]).then(([local, summary]) => {
      let version = null;
      try {
        const j = JSON.parse(local.body);
        version = j.product === "hiveswarm" ? j.version || null : null;
      } catch {
        /* an older app that does not say what it is */
      }
      return { ui: local.ok, daemon: summary.ok, version };
    });
  }

  // ── running commands in the engine's environment ────────────────────────

  /**
   * The preamble every engine script starts with: the user's interactive PATH, then the engine's venv and the usual
   * bin directories in front of it, so the engine's own hm always wins over an older one elsewhere on PATH.
   */
  pre() {
    return (this.userPath ? `export PATH=${shQuote(this.userPath)}:"$PATH"; ` : "") + HM_PATH;
  }

  /** argv for running `script` in a login shell, through WSL on Windows. */
  argv(script, { interactive = false } = {}) {
    let shell = (this.settings.shell || "bash -lc").split(/\s+/).filter(Boolean);
    if (interactive && !this.settings.shell) shell = ["bash", "-lic"];
    if (this.platform === "win32") {
      const a = [];
      if (this.settings.distro) a.push("-d", this.settings.distro);
      if (this.settings.user) a.push("-u", this.settings.user);
      a.push("-e", ...shell, script);
      return { cmd: this.wslPath, args: a };
    }
    return { cmd: shell[0], args: [...shell.slice(1), script] };
  }

  /** Run a script to completion; resolves {code, out}. Output is logged unless quiet. */
  run(script, { quiet = false, timeoutMs = 0, interactive = false } = {}) {
    const { cmd, args } = this.argv(script, { interactive });
    return new Promise((resolve) => {
      let out = "";
      let p;
      try {
        p = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
      } catch (e) {
        resolve({ code: -1, out: String(e) });
        return;
      }
      const onData = (buf) => {
        const text = buf.toString("utf8");
        out += text;
        if (!quiet) text.split(/\r?\n/).forEach((l) => this.log(l));
      };
      p.stdout.on("data", onData);
      p.stderr.on("data", onData);
      let timer = null;
      if (timeoutMs) timer = setTimeout(() => p.kill(), timeoutMs);
      p.on("error", (e) => {
        if (timer) clearTimeout(timer);
        resolve({ code: -1, out: out + String(e) });
      });
      p.on("close", (code) => {
        if (timer) clearTimeout(timer);
        resolve({ code: code == null ? -1 : code, out });
      });
    });
  }

  /**
   * The PATH of the user's interactive shell (what their terminal sees, including nvm, npm-global, conda and
   * anything else .bashrc adds), so the worker finds the same agent CLIs the user does.
   */
  async captureUserPath() {
    const r = await this.run(`printf '${MARK}%s${MARK}' "$PATH"`, { quiet: true, timeoutMs: 30000, interactive: true });
    const m = new RegExp(`${MARK}(.*?)${MARK}`, "s").exec(r.out);
    this.userPath = m ? cleanPath(m[1], this.platform) : "";
    if (this.userPath) this.log(`using your shell's PATH (${this.userPath.split(":").length} directories)`);
    return this.userPath;
  }

  /** WSL distros on this machine (Windows only). */
  listDistros() {
    if (this.platform !== "win32") return [];
    try {
      const r = spawnSync(this.wslPath, ["-l", "-q"], { encoding: "utf16le", windowsHide: true, timeout: 10000 });
      return String(r.stdout || "")
        .split(/\r?\n/)
        .map((s) => s.replace(/\0/g, "").trim())
        .filter(Boolean);
    } catch {
      return [];
    }
  }

  /** The bundled wheel on the host: {hostPath, version} or null. */
  bundledWheel() {
    const dir = path.join(this.resourcesPath, "engine");
    let files = [];
    try {
      files = fs.readdirSync(dir).filter((f) => f.endsWith(".whl"));
    } catch {
      return null;
    }
    if (!files.length) return null;
    const name = files.sort().reverse()[0];
    const m = /^hiveswarm-([^-]+)-/.exec(name);
    return { hostPath: path.join(dir, name), version: m ? m[1] : "" };
  }

  /** The bundled wheel, as a path the engine's shell can open. */
  async wheelPath() {
    const w = this.bundledWheel();
    if (!w) return null;
    if (this.platform !== "win32") return w.hostPath;
    const r = await this.run(`wslpath -u ${shQuote(w.hostPath)}`, { quiet: true, timeoutMs: 15000 });
    const p = r.out.trim().split(/\r?\n/).pop();
    return r.code === 0 && p ? p : null;
  }

  /** Which hm is installed: "hiveswarm <version>", "other" (a pre-rename hivemind hm), or null. */
  async installedVersion() {
    const r = await this.run(
      `${this.pre()}; if command -v hm >/dev/null 2>&1; then v=$(hm --version 2>/dev/null | grep -o 'hiveswarm [0-9][0-9.a-z]*'); echo "HM:\${v:-other}"; else echo HM:none; fi`,
      { quiet: true, timeoutMs: 60000 },
    );
    const m = /HM:(hiveswarm (\S+)|other|none)/.exec(r.out);
    if (!m) return { ok: false, out: r.out };
    if (m[1] === "none") return { ok: true, version: null };
    if (m[1] === "other") return { ok: true, version: "other" };
    return { ok: true, version: m[2] };
  }

  // ── the boot sequence ──────────────────────────────────────────────────

  /** Ensure the engine is installed and configured; resolves true when `hm up` can be started. */
  async prepare() {
    this.setState("checking", "looking for the engine");
    this.log(this.platform === "win32"
      ? `checking WSL (${this.settings.distro || "default distro"}${this.settings.user ? ", user " + this.settings.user : ""}) for the engine`
      : "checking this machine for the engine");
    if (this.platform === "win32" && !fs.existsSync(this.wslPath)) {
      this.setState("failed", "WSL is not installed. Install it from an admin PowerShell with: wsl --install");
      return false;
    }
    await this.captureUserPath();
    const iv = await this.installedVersion();
    if (!iv.ok) {
      this.setState("failed", this.platform === "win32"
        ? `could not run a shell in WSL (${this.settings.distro || "default distro"}): ${iv.out.trim().slice(0, 300) || "no output"}`
        : `could not run a login shell: ${iv.out.trim().slice(0, 300) || "no output"}`);
      return false;
    }
    const bundled = this.bundledWheel();
    if (!iv.version || iv.version === "other") {
      if (iv.version === "other") this.log("found a pre-rename hm (Hivemind); installing Hiveswarm alongside it");
      if (!(await this.install())) return false;
    } else if (bundled && bundled.version && iv.version !== bundled.version) {
      this.log(`engine ${iv.version} installed, app ships ${bundled.version}: upgrading`);
      if (!(await this.install())) return false;
    } else {
      this.log(`engine found: hiveswarm ${iv.version}`);
    }
    const cfg = await this.run(
      'H="${HIVESWARM_HOME:-$HOME/.hiveswarm}"; L="$HOME/.config/hivemind"; mkdir -p "$HOME/.local/bin"; '
      + 'for b in hm hiveswarm-worker hiveswarm-mcp hiveswarm-daemon hiveswarm-decide; do '
      + '[ -x "$H/venv/bin/$b" ] && [ "$(readlink "$HOME/.local/bin/$b")" != "$H/venv/bin/$b" ] && ln -sf "$H/venv/bin/$b" "$HOME/.local/bin/$b"; done; '
      + 'if [ -d "$L" ] && { [ ! -f "$H/worker.toml" ] || systemctl --user is-active --quiet hivemind-worker 2>/dev/null || systemctl --user is-active --quiet hivemind-ui 2>/dev/null; }; then echo CFG:legacy; '
      + 'elif [ -f "$H/config.toml" ] || [ -f "$H/worker.toml" ] || [ -f "$HOME/.config/hiveswarm/worker.toml" ]; then echo CFG:ok; '
      + "else echo CFG:none; fi",
      { quiet: true, timeoutMs: 30000 },
    );
    if (/CFG:legacy/.test(cfg.out)) {
      this.setState("migrating", "bringing your Hivemind setup into ~/.hiveswarm (copies; nothing is deleted)");
      const m = await this.run(`${this.pre()}; hm migrate`, { timeoutMs: 120000, interactive: true });
      if (m.code !== 0) {
        this.setState("failed", "hm migrate failed; see the log");
        return false;
      }
    } else if (/CFG:none/.test(cfg.out)) {
      this.setState("initialising", "first run: writing ~/.hiveswarm/config.toml and a demo project");
      const init = await this.run(`${this.pre()}; hm init --project demo`, { timeoutMs: 120000 });
      if (init.code !== 0) {
        this.setState("failed", "hm init failed; see the log");
        return false;
      }
    }
    return true;
  }

  /** Install the bundled wheel into ~/.hiveswarm/venv and link the entry points into ~/.local/bin. */
  async install() {
    this.setState("installing", "installing the engine into ~/.hiveswarm/venv (one time)");
    const wheel = await this.wheelPath();
    if (!wheel) {
      this.setState("failed", "the app has no bundled engine wheel; download the installer from the releases page");
      return false;
    }
    this.log(`engine wheel: ${wheel}`);
    const script = [
      "set -e",
      this.pre(),
      'H="${HIVESWARM_HOME:-$HOME/.hiveswarm}"',
      'mkdir -p "$H" "$HOME/.local/bin"',
      'py_ok() { command -v python3 >/dev/null 2>&1 && python3 -c "import sys, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >/dev/null 2>&1; }',
      'if ! command -v uv >/dev/null 2>&1 && ! py_ok; then',
      '  echo "no uv and no Python 3.11+ with venv: installing uv (a single binary in ~/.local/bin) to provide one"',
      '  if command -v curl >/dev/null 2>&1; then curl -LsSf https://astral.sh/uv/install.sh | sh; else wget -qO- https://astral.sh/uv/install.sh | sh; fi',
      '  export PATH="$HOME/.local/bin:$PATH"',
      "fi",
      'if command -v uv >/dev/null 2>&1; then',
      '  echo "using uv to create $H/venv"',
      '  test -x "$H/venv/bin/python" || uv venv --quiet --python ">=3.11" "$H/venv"',
      '  echo "installing hiveswarm and its dependencies (about a minute the first time)"',
      `  uv pip install --quiet --python "$H/venv/bin/python" --upgrade --reinstall-package hiveswarm ${shQuote(wheel)}`,
      "else",
      '  echo "using $(python3 --version) to create $H/venv"',
      '  test -x "$H/venv/bin/python" || python3 -m venv "$H/venv"',
      '  echo "installing hiveswarm and its dependencies (about a minute the first time)"',
      '  "$H/venv/bin/python" -m pip install --quiet --upgrade pip',
      `  "$H/venv/bin/python" -m pip install --quiet --upgrade --force-reinstall --no-deps ${shQuote(wheel)}`,
      `  "$H/venv/bin/python" -m pip install --quiet ${shQuote(wheel)}`,
      "fi",
      'echo "linking hm into $HOME/.local/bin"',
      'for b in hm hiveswarm-worker hiveswarm-mcp hiveswarm-daemon hiveswarm-decide; do ln -sf "$H/venv/bin/$b" "$HOME/.local/bin/$b"; done',
      '"$H/venv/bin/hm" --version',
    ].join("\n");
    const r = await this.run(script, { timeoutMs: 15 * 60 * 1000 });
    if (r.code !== 0) {
      this.setState("failed", "installing the engine failed; see the log (usually no network, or python3-venv missing without uv)");
      return false;
    }
    this.log("engine installed");
    return true;
  }

  /** Start `hm up` and resolve once the app answers (or false on failure). */
  async start() {
    if (this.proc) return true;
    const ok = await this.prepare();
    if (!ok) return false;
    this.setState("starting", "starting the engine");
    const script = `${this.pre()}; exec hm up --no-open --ui-port ${Number(this.settings.port)}`;
    const { cmd, args } = this.argv(script);
    this.log(`$ hm up --no-open --ui-port ${Number(this.settings.port)}`);
    this._stopping = false;
    try {
      this.proc = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    } catch (e) {
      this.setState("failed", `could not start the engine: ${e}`);
      return false;
    }
    const onData = (buf) => buf.toString("utf8").split(/\r?\n/).forEach((l) => this.log(l));
    this.proc.stdout.on("data", onData);
    this.proc.stderr.on("data", onData);
    this.proc.on("close", (code) => {
      this.proc = null;
      if (this._stopping) {
        this.setState("stopped", "engine stopped");
      } else {
        this.setState("failed", `the engine exited with code ${code}; see the log`);
      }
    });
    const deadline = Date.now() + 90000;
    while (Date.now() < deadline) {
      if (!this.proc) return false;
      const h = await this.health();
      if (h.ui) {
        this.setState("running", h.daemon ? "engine running" : "app up; waiting for the daemon");
        return true;
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
    this.setState("failed", "the app did not come up within 90 s; see the log");
    return false;
  }

  /** Stop the engine we started (SIGINT so hm up shuts its children down cleanly). */
  async stop() {
    if (!this.proc) return;
    this._stopping = true;
    if (this.platform === "win32") {
      await this.run(`pkill -INT -f 'hm up --no-open' || true`, { quiet: true, timeoutMs: 15000 });
    } else {
      try {
        this.proc.kill("SIGINT");
      } catch {
        /* already gone */
      }
    }
    const deadline = Date.now() + 10000;
    while (this.proc && Date.now() < deadline) await new Promise((r) => setTimeout(r, 200));
    if (this.proc) {
      try {
        this.proc.kill("SIGKILL");
      } catch {
        /* ignore */
      }
    }
  }

  /** Stop an engine this app did not start (left running by an older version, or started in a terminal). */
  async stopExternal() {
    await this.run(`pkill -INT -f 'hm up --no-open' || true; sleep 3`, { quiet: true, timeoutMs: 20000 });
  }

  async restart() {
    await this.stop();
    return this.start();
  }
}

module.exports = { Engine, DEFAULTS, shQuote, cleanPath, HM_PATH };
