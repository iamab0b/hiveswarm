import { useEffect, useRef, useState } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { ClipboardAddon } from "@xterm/addon-clipboard";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

async function copyText(text: string): Promise<boolean> {
  if (!text) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

async function readClipboard(): Promise<string | null> {
  try {
    if (navigator.clipboard && window.isSecureContext) return await navigator.clipboard.readText();
  } catch {
    /* denied or unavailable */
  }
  return null;
}

const DARK = {
  background: "#0b0e13",
  foreground: "#dfe4ec",
  cursor: "#f2b544",
  cursorAccent: "#0b0e13",
  selectionBackground: "rgba(242,181,68,0.30)",
  black: "#1a1f29", red: "#ff5c7a", green: "#3ddc97", yellow: "#f2b544", blue: "#6ea8ff", magenta: "#c792ea", cyan: "#5fd7e0", white: "#c9d1dc",
  brightBlack: "#5c6677", brightRed: "#ff7b93", brightGreen: "#6fe7b3", brightYellow: "#ffd076", brightBlue: "#93bfff", brightMagenta: "#d9b3f5", brightCyan: "#8ee9f0", brightWhite: "#f2f5f9",
};
const LIGHT = {
  background: "#ffffff",
  foreground: "#1c2230",
  cursor: "#d99a1e",
  cursorAccent: "#ffffff",
  selectionBackground: "rgba(217,154,30,0.30)",
  black: "#1c2230", red: "#c8324f", green: "#1f9d6a", yellow: "#b07900", blue: "#2f6fdb", magenta: "#8a4fc9", cyan: "#0f8f9a", white: "#d0d5dd",
  brightBlack: "#6b7382", brightRed: "#e2536f", brightGreen: "#2cb47f", brightYellow: "#c98a00", brightBlue: "#4f88ec", brightMagenta: "#a26ad8", brightCyan: "#2aa5b0", brightWhite: "#f5f7fa",
};

export function Terminal({ tid, readOnly = false, fontSize = 13, className, dark = true, onStatus }: {
  tid: string;
  readOnly?: boolean;
  fontSize?: number;
  className?: string;
  dark?: boolean;
  onStatus?: (s: "connecting" | "open" | "closed" | "error", msg?: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<{ s: "connecting" | "open" | "closed" | "error"; msg?: string }>({ s: "connecting" });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const term = new XTerm({
      fontFamily: '"JetBrains Mono Variable", ui-monospace, Menlo, monospace',
      fontSize,
      lineHeight: 1.25,
      letterSpacing: 0,
      cursorBlink: !readOnly,
      cursorStyle: "bar",
      scrollback: 5000,
      theme: dark ? DARK : LIGHT,
      allowProposedApi: true,
      disableStdin: readOnly,
      minimumContrastRatio: 1,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    term.loadAddon(new ClipboardAddon(undefined, {
      readText: async () => "",
      writeText: async (_sel, text) => { await copyText(text); },
    }));
    term.open(el);
    let webgl: { dispose: () => void } | null = null;
    import("@xterm/addon-webgl")
      .then((m) => {
        try {
          const addon = new m.WebglAddon();
          addon.onContextLoss(() => addon.dispose());
          term.loadAddon(addon);
          webgl = addon;
        } catch {
          /* canvas fallback */
        }
      })
      .catch(() => undefined);
    fit.fit();
    termRef.current = term;
    const copySelection = (clear = true) => {
      const sel = term.getSelection();
      if (!sel) return false;
      void copyText(sel);
      if (clear) term.clearSelection();
      return true;
    };
    const pasteClipboard = async () => {
      if (readOnly) return;
      const text = await readClipboard();
      if (text) term.paste(text);
      else toast("Paste with Ctrl+Shift+V (the browser only lets the page read the clipboard on localhost or https)", { duration: 4000 });
    };
    term.attachCustomKeyEventHandler((ev) => {
      if (ev.type !== "keydown") return true;
      const mod = ev.ctrlKey || ev.metaKey;
      if (ev.key === "Escape" && ev.shiftKey) {
        term.blur();
        (document.activeElement as HTMLElement | null)?.blur();
        return false;
      }
      if (mod && ev.shiftKey && (ev.key === "C" || ev.key === "c")) {
        ev.preventDefault();
        copySelection();
        return false;
      }
      if (mod && !ev.shiftKey && !ev.altKey && ev.key === "c" && term.hasSelection()) {
        ev.preventDefault();
        copySelection();
        return false;
      }
      if (ev.key === "Insert" && mod && !ev.shiftKey) {
        ev.preventDefault();
        copySelection();
        return false;
      }
      if (mod && ev.shiftKey && (ev.key === "V" || ev.key === "v")) {
        return true;
      }
      return true;
    });
    const onMouseUp = () => {
      if (term.hasSelection()) copySelection(false);
    };
    const onContextMenu = (ev: MouseEvent) => {
      ev.preventDefault();
      if (term.hasSelection()) {
        copySelection();
        return;
      }
      void pasteClipboard();
    };
    el.addEventListener("mouseup", onMouseUp);
    el.addEventListener("contextmenu", onContextMenu);

    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const q = readOnly ? `?mode=snapshot&cols=${term.cols}&rows=${term.rows}` : "";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/term/${tid}${q}`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;
    const report = (s: "connecting" | "open" | "closed" | "error", msg?: string) => {
      setStatus({ s, msg });
      onStatus?.(s, msg);
    };
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      report("open");
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        try {
          const m = JSON.parse(ev.data);
          if (m.type === "error") {
            term.write(`\r\n\x1b[33m${m.message}\x1b[0m\r\n`);
            report("error", m.message);
            return;
          }
        } catch {
          term.write(ev.data);
        }
        return;
      }
      term.write(new Uint8Array(ev.data));
    };
    ws.onclose = () => report("closed");
    ws.onerror = () => report("error", "connection failed");
    const dataSub = term.onData((d) => {
      if (readOnly) return;
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "input", data: d }));
    });
    const binSub = term.onBinary((d) => {
      if (readOnly) return;
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "input", data: d }));
    });
    const resizeSub = term.onResize(({ cols, rows }) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "resize", cols, rows }));
    });
    const ro = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch {
        /* ignore */
      }
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      el.removeEventListener("mouseup", onMouseUp);
      el.removeEventListener("contextmenu", onContextMenu);
      dataSub.dispose();
      binSub.dispose();
      resizeSub.dispose();
      try {
        ws.close();
      } catch {
        /* ignore */
      }
      webgl?.dispose();
      term.dispose();
      termRef.current = null;
    };
  }, [tid, readOnly, fontSize, dark]);

  return (
    <div className={cn("group relative h-full w-full overflow-hidden", dark ? "bg-[#0b0e13]" : "bg-white", className)}>
      <div ref={ref} className="h-full w-full" onClick={() => !readOnly && termRef.current?.focus()} />
      {!readOnly && status.s === "open" ? (
        <div className="pointer-events-none absolute right-2 top-1.5 rounded-full border border-border bg-surface/80 px-2 py-0.5 text-[10.5px] text-dim opacity-0 transition-opacity group-hover:opacity-100">
          live terminal · select copies · right-click pastes · ⇧esc leaves
        </div>
      ) : null}
      {status.s !== "open" ? (
        <div className="pointer-events-none absolute inset-x-0 bottom-2 flex justify-center">
          <span className="rounded-full border border-border bg-surface/90 px-2.5 py-0.5 text-[11px] text-muted backdrop-blur">
            {status.s === "connecting" ? "connecting to terminal…" : status.msg || (status.s === "closed" ? "terminal closed" : "terminal error")}
          </span>
        </div>
      ) : null}
    </div>
  );
}
