"use strict";
/**
 * The engine manager: finds or installs the Hiveswarm engine (the Python package) and runs `hm up` for the app.
 *
 * On Windows the engine lives in WSL2 (that is where the agent CLIs are), so every command goes through
 * `wsl.exe -d <distro> -u <user> -- bash -lic '<command>'`. On Linux and macOS it runs directly in a login shell.
 * The engine is installed into its own virtualenv at ~/.hiveswarm/venv from the wheel bundled with the app, and
 * `~/.local/bin/hm` is linked to it so the CLI works from any terminal too.
 *
 * This module has no Electron dependency so it can be unit-tested with plain node.
 */
const { spawn, spawnSync } = require("node:child_process");
const http = require("node:http");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
const { EventEmitter } = require("node:events");

const DEFAULTS = {
  distro: "",          // WSL distro name; "" = the default distro
  user: "",            // WSL user; "" = the distro's default user
  port: 7790,          // the app's port on 127.0.0.1
  keepRunning: false,  // leave the engine running when the app quits
  openAtLogin: false,
  shell: "",           // override the shell command, e.g. "bash -lic"
};

// Every engine command starts with this: the engine's venv and the places agent CLIs get installed to, plus nvm
// (where Claude Code, Codex and Gemini usually live) — a non-interactive login shell would not load it otherwise.
const HM_PATH = 'export PATH="$HOME/.hiveswarm/venv/bin:$HOME/.local/bin:$HOME/.bun/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"; '
  + '[ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1; true';

function shQuote(s) {
  return "'" + String(s).replace(/'/g, `'\\''`) + "'";
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
    this.state = "stopped"; // stopped | checking | installing | initialising | starting | running | failed
    this.detail = "";
    this.proc = null;
    this.lines = [];
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

  /** Is the app server answering on the port? Resolves to {ui, daemon} booleans. */
  health() {
    const get = (p) =>
      new Promise((resolve) => {
        const req = http.get({ host: "127.0.0.1", port: this.settings.port, path: p, timeout: 1500 }, (res) => {
          res.resume();
          resolve(res.statusCode === 200);
        });
        req.on("error", () => resolve(false));
        req.on("timeout", () => {
          req.destroy();
          resolve(false);
        });
      });
    return Promise.all([get("/api/local"), get("/api/summary")]).then(([ui, daemon]) => ({ ui, daemon }));
  }

  // ── running commands in the engine's environment ────────────────────────

  /** argv for running `script` in a login+interactive shell, through WSL on Windows. */
  argv(script) {
    const shell = (this.settings.shell || "bash -lc").split(/\s+/);
    if (this.platform === "win32") {
      const a = [];
      if (this.settings.distro) a.push("-d", this.settings.distro);
      if (this.settings.user) a.push("-u", this.settings.user);
      a.push("--", ...shell, script);
      return { cmd: this.wslPath, args: a };
    }
    return { cmd: shell[0], args: [...shell.slice(1), script] };
  }

  /** Run a script to completion; resolves {code, out}. Output is logged. */
  run(script, { quiet = false, timeoutMs = 0 } = {}) {
    const { cmd, args } = this.argv(script);
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
    const hostPath = w.hostPath;
    if (this.platform !== "win32") return hostPath;
    const r = await this.run(`wslpath -u ${shQuote(hostPath)}`, { quiet: true, timeoutMs: 15000 });
    const p = r.out.trim().split(/\r?\n/).pop();
    return r.code === 0 && p ? p : null;
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
    const probe = await this.run(`${HM_PATH}; command -v hm >/dev/null 2>&1 && hm --version || echo NOHM`, { quiet: true, timeoutMs: 60000 });
    const found = /hiveswarm (\S+)/.exec(probe.out);
    if (probe.code === -1 || (!found && !/NOHM/.test(probe.out))) {
      this.setState("failed", this.platform === "win32"
        ? `could not run a shell in WSL (${this.settings.distro || "default distro"}): ${probe.out.trim().slice(0, 300) || "no output"}`
        : `could not run a login shell: ${probe.out.trim().slice(0, 300) || "no output"}`);
      return false;
    }
    const bundled = this.bundledWheel();
    if (!found) {
      const ok = await this.install();
      if (!ok) return false;
    } else if (bundled && bundled.version && found[1] !== bundled.version) {
      this.log(`engine ${found[1]} installed, app ships ${bundled.version}: upgrading`);
      const ok = await this.install();
      if (!ok) return false;
    } else {
      this.log(`engine found: hiveswarm ${found[1]}`);
    }
    const cfg = await this.run(`test -f "\${HIVESWARM_HOME:-$HOME/.hiveswarm}/config.toml" && echo HASCFG || echo NOCFG`, { quiet: true, timeoutMs: 30000 });
    if (/NOCFG/.test(cfg.out)) {
      this.setState("initialising", "first run: writing ~/.hiveswarm/config.toml and a demo project");
      const init = await this.run(`${HM_PATH}; hm init --project demo`, { timeoutMs: 120000 });
      if (init.code !== 0) {
        this.setState("failed", "hm init failed; see the log");
        return false;
      }
    }
    return true;
  }

  /** Install the bundled wheel into ~/.hiveswarm/venv and link hm into ~/.local/bin. */
  async install() {
    this.setState("installing", "installing the engine into ~/.hiveswarm/venv (one time)");
    const wheel = await this.wheelPath();
    if (!wheel) {
      this.setState("failed", "the app has no bundled engine wheel; download the installer from the releases page");
      return false;
    }
    const py = await this.run(`command -v python3 >/dev/null && python3 -c 'import sys; print("PY", sys.version_info[0], sys.version_info[1])' || echo NOPY`, { quiet: true, timeoutMs: 30000 });
    const m = /PY (\d+) (\d+)/.exec(py.out);
    if (!m || Number(m[1]) < 3 || Number(m[2]) < 11) {
      this.setState("failed", this.platform === "win32"
        ? "Python 3.11+ is missing in WSL. In the WSL terminal run: sudo apt update && sudo apt install -y python3 python3-venv git tmux"
        : "Python 3.11+ is missing. Install it (and git, tmux) with your package manager, then reopen Hiveswarm");
      return false;
    }
    const venvOk = await this.run(`python3 -c 'import venv, ensurepip' >/dev/null 2>&1 && echo VENVOK || echo NOVENV`, { quiet: true, timeoutMs: 30000 });
    if (/NOVENV/.test(venvOk.out)) {
      this.setState("failed", "python3-venv is missing. In the WSL terminal run: sudo apt install -y python3-venv");
      return false;
    }
    this.log(`engine wheel: ${wheel}`);
    const script = [
      "set -e",
      'H="${HIVESWARM_HOME:-$HOME/.hiveswarm}"',
      'mkdir -p "$H" "$HOME/.local/bin"',
      'echo "creating $H/venv"',
      'test -x "$H/venv/bin/python" || python3 -m venv "$H/venv"',
      'echo "installing hiveswarm and its dependencies (about a minute the first time)"',
      `"$H/venv/bin/python" -m pip install --quiet --upgrade pip`,
      `"$H/venv/bin/python" -m pip install --quiet --upgrade ${shQuote(wheel)}`,
      'echo "linking hm into $HOME/.local/bin"',
      'ln -sf "$H/venv/bin/hm" "$HOME/.local/bin/hm"',
      'for b in hiveswarm-worker hiveswarm-mcp hiveswarm-daemon hiveswarm-decide; do ln -sf "$H/venv/bin/$b" "$HOME/.local/bin/$b"; done',
      '"$H/venv/bin/hm" --version',
    ].join("; ");
    const r = await this.run(script, { timeoutMs: 15 * 60 * 1000 });
    if (r.code !== 0) {
      this.setState("failed", "installing the engine failed; see the log (usually a missing python3-venv or no network)");
      return false;
    }
    this.log("engine installed");
    return true;
  }

  /** Start `hm up` and resolve once the app answers (or reject on failure). */
  async start() {
    if (this.proc) return true;
    const ok = await this.prepare();
    if (!ok) return false;
    this.setState("starting", "starting the decide service, daemon, worker and app");
    const script = `${HM_PATH}; exec hm up --no-open --ui-port ${Number(this.settings.port)}`;
    const { cmd, args } = this.argv(script);
    this.log(`$ ${[cmd, ...args].join(" ")}`);
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

  /** Stop a swarm this app did not start (an engine left running from a previous session). */
  async stopExternal() {
    await this.run(`pkill -INT -f 'hm up --no-open' || true`, { quiet: true, timeoutMs: 15000 });
  }

  async restart() {
    await this.stop();
    return this.start();
  }
}

module.exports = { Engine, DEFAULTS, shQuote, HM_PATH };
