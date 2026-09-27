"use strict";
const { app, BrowserWindow, Tray, Menu, shell, ipcMain, nativeImage } = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const { Engine, DEFAULTS } = require("./engine");

let win = null;
let tray = null;
let engine = null;
let quitting = false;

const settingsPath = () => path.join(app.getPath("userData"), "settings.json");

function loadSettings() {
  try {
    return { ...DEFAULTS, ...JSON.parse(fs.readFileSync(settingsPath(), "utf8")) };
  } catch {
    return { ...DEFAULTS };
  }
}

function saveSettings(s) {
  fs.mkdirSync(path.dirname(settingsPath()), { recursive: true });
  fs.writeFileSync(settingsPath(), JSON.stringify(s, null, 2));
}

function bootUrl() {
  return "file://" + path.join(__dirname, "boot.html");
}

function send(channel, payload) {
  if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
}

function createWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 900,
    minHeight: 600,
    title: "Hiveswarm",
    backgroundColor: "#0a0c10",
    icon: path.join(__dirname, "..", "assets", "icon.png"),
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  win.loadURL(bootUrl());
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url) && !url.startsWith(engine.url)) {
      shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });
  win.webContents.on("will-navigate", (ev, url) => {
    if (!url.startsWith(engine.url) && !url.startsWith("file://")) {
      ev.preventDefault();
      shell.openExternal(url);
    }
  });
  win.on("close", (ev) => {
    if (!quitting && process.platform !== "darwin" && tray) {
      ev.preventDefault();
      win.hide();
    }
  });
  win.on("closed", () => {
    win = null;
  });
}

function showApp() {
  if (!win) createWindow();
  win.show();
  win.focus();
}

function openEngineLog() {
  shell.openPath(engine.logPath);
}

function buildTray() {
  const icon = nativeImage.createFromPath(path.join(__dirname, "..", "assets", "tray.png")).resize({ width: 16, height: 16 });
  tray = new Tray(icon);
  tray.setToolTip("Hiveswarm");
  const refresh = () => {
    const running = engine.state === "running";
    tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: "Open Hiveswarm", click: showApp },
        { type: "separator" },
        { label: `Engine: ${engine.state}${engine.detail ? " — " + engine.detail : ""}`, enabled: false },
        { label: running ? "Restart engine" : "Start engine", click: () => boot(true) },
        { label: "Stop engine", enabled: running || engine.state === "starting", click: () => engine.stop() },
        { label: "Engine log", click: openEngineLog },
        { type: "separator" },
        { label: "Quit and stop the swarm", click: () => quit(false) },
        { label: "Quit, keep the swarm running", click: () => quit(true) },
      ]),
    );
  };
  refresh();
  engine.on("state", refresh);
  tray.on("click", showApp);
}

async function boot(restart = false) {
  send("engine:state", { state: engine.state, detail: engine.detail });
  if (restart) {
    if (win) win.loadURL(bootUrl());
    await engine.stop();
  }
  const h = await engine.health();
  if (h.ui && !restart) {
    engine.setState("running", "engine already running");
    engine.log(`found the app already up at ${engine.url}`);
    win && win.loadURL(engine.url);
    return;
  }
  const ok = await engine.start();
  if (ok && win) win.loadURL(engine.url);
  else if (win) win.loadURL(bootUrl());
}

async function quit(keepRunning) {
  quitting = true;
  if (!keepRunning) await engine.stop();
  app.quit();
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", showApp);

  app.whenReady().then(() => {
    const settings = loadSettings();
    engine = new Engine({
      resourcesPath: app.isPackaged ? process.resourcesPath : path.join(__dirname, "..", ".."),
      logPath: path.join(app.getPath("userData"), "logs", "engine.log"),
      settings,
    });
    engine.on("state", (s) => {
      send("engine:state", s);
      if (s.state === "failed" && win && !win.webContents.getURL().startsWith("file://")) win.loadURL(bootUrl());
    });
    engine.on("log", (line) => send("engine:log", line));

    ipcMain.handle("engine:status", () => ({ state: engine.state, detail: engine.detail, lines: engine.lines.slice(-200), url: engine.url }));
    ipcMain.handle("engine:settings", () => ({ ...engine.settings, distros: engine.listDistros(), platform: process.platform, version: app.getVersion() }));
    ipcMain.handle("engine:saveSettings", async (_ev, next) => {
      const merged = { ...engine.settings, ...next, port: Number(next.port) || DEFAULTS.port };
      saveSettings(merged);
      engine.settings = merged;
      try {
        app.setLoginItemSettings({ openAtLogin: !!merged.openAtLogin });
      } catch {
        /* not supported on this platform */
      }
      return merged;
    });
    ipcMain.handle("engine:retry", () => boot(true));
    ipcMain.handle("engine:stop", () => engine.stop());
    ipcMain.handle("engine:openLog", () => openEngineLog());
    ipcMain.handle("engine:openExternal", (_ev, url) => shell.openExternal(url));

    createWindow();
    buildTray();
    boot();
  });

  app.on("activate", showApp);
  app.on("before-quit", () => {
    quitting = true;
  });
  app.on("will-quit", async (ev) => {
    if (engine && engine.proc && !engine.settings.keepRunning && !engine._stopping) {
      ev.preventDefault();
      await engine.stop();
      app.quit();
    }
  });
  app.on("window-all-closed", () => {
    if (process.platform === "darwin") return;
    if (!tray) app.quit();
  });
}

for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => {
    quit(engine ? !!engine.settings.keepRunning : true);
  });
}

process.on("unhandledRejection", (e) => {
  try {
    engine && engine.log(`unhandled: ${e && e.stack ? e.stack : e}`);
  } catch {
    /* ignore */
  }
});


