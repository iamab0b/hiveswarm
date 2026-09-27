"use strict";
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("hiveswarmDesktop", {
  status: () => ipcRenderer.invoke("engine:status"),
  settings: () => ipcRenderer.invoke("engine:settings"),
  saveSettings: (s) => ipcRenderer.invoke("engine:saveSettings", s),
  retry: () => ipcRenderer.invoke("engine:retry"),
  stop: () => ipcRenderer.invoke("engine:stop"),
  openLog: () => ipcRenderer.invoke("engine:openLog"),
  openExternal: (url) => ipcRenderer.invoke("engine:openExternal", url),
  onState: (fn) => ipcRenderer.on("engine:state", (_ev, s) => fn(s)),
  onLog: (fn) => ipcRenderer.on("engine:log", (_ev, line) => fn(line)),
});
