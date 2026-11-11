import { useEffect, useRef, useState } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { ClipboardAddon } from "@xterm/addon-clipboard";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { isAppCombo } from "@/lib/keys";

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

type Status = "connecting" | "open" | "closed" | "error";

/**
 * An agent's tmux window in the page.
 *
 * `attach` is the real terminal: keys go to the agent and the agent's terminal takes the size of this element.
 * `mini` is a live miniature for a tile too small to work in: the agent's screen at its own size, drawn scaled
 * down to fit, read-only, and without resizing anything on the agent's side.
 */
export function Terminal({ tid, mode = "attach", fontSize = 13, className, dark = true, focused, onFocus, onStatus }: {
  tid: string;
  mode?: "attach" | "mini";
  fontSize?: number;
  className?: string;
  dark?: boolean;
  /** attach mode: take the keyboard when this becomes true */
  focused?: boolean;
  onFocus?: () => void;
  onStatus?: (s: Status, msg?: string) => void;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const ref = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const onFocusRef = useRef(onFocus);
  onFocusRef.current = onFocus;
  const onStatusRef = useRef(onStatus);
  onStatusRef.current = onStatus;
  // true while the app itself hands this terminal the keyboard, so that is not reported as you focusing it
  const handing = useRef(false);
  const [status, setStatus] = useState<{ s: Status; msg?: string }>({ s: "connecting" });
  const mini = mode === "mini";

  useEffect(() => {
    const el = ref.current;
    const box = boxRef.current;
    if (!el || !box) return;
    const term = new XTerm({
      fontFamily: '"JetBrains Mono Variable", ui-monospace, Menlo, monospace',
      fontSize,
      lineHeight: 1.25,
      letterSpacing: 0,
      cursorBlink: !mini,
      cursorStyle: "bar",
      cursorInactiveStyle: mini ? "none" : "outline",
      scrollback: mini ? 0 : 5000,
      theme: dark ? DARK : LIGHT,
      allowProposedApi: true,
      disableStdin: mini,
      minimumContrastRatio: 1,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    if (!mini) {
      term.loadAddon(new WebLinksAddon());
      term.loadAddon(new ClipboardAddon(undefined, {
        readText: async () => "",
        writeText: async (_sel, text) => { await copyText(text); },
      }));
    }
    term.open(el);
    let webgl: { dispose: () => void } | null = null;
    let gone = false;
    // The size of a cell changes when the renderer does and when the font arrives, so the terminal is fitted to
    // its tile again after each: otherwise it keeps the column count of the first measurement and leaves a strip
    // of the tile unused.
    const refit = () => {
      if (gone || mini) return;
      try {
        if (box.clientWidth > 40 && box.clientHeight > 30) fit.fit();
      } catch {
        /* not laid out yet */
      }
    };
    if (!mini) {
      import("@xterm/addon-webgl")
        .then((m) => {
          if (gone) return;
          try {
            const addon = new m.WebglAddon();
            addon.onContextLoss(() => { addon.dispose(); requestAnimationFrame(refit); });
            term.loadAddon(addon);
            webgl = addon;
            requestAnimationFrame(refit);
          } catch {
            /* canvas fallback */
          }
        })
        .catch(() => undefined);
      refit();
    }
    termRef.current = term;

    // mini: draw the agent's screen at its own size and scale the picture to the tile
    const rescale = () => {
      if (!mini) return;
      const screen = el.querySelector(".xterm-screen") as HTMLElement | null;
      const w = (screen?.offsetWidth || 0) + 20;
      const h = (screen?.offsetHeight || 0) + 16;
      if (w <= 20 || h <= 16 || !box.clientWidth || !box.clientHeight) return;
      const k = Math.min(1, box.clientWidth / w, box.clientHeight / h);
      el.style.width = `${w}px`;
      el.style.height = `${h}px`;
      el.style.transform = `scale(${k})`;
    };
    document.fonts?.ready.then(() => requestAnimationFrame(mini ? rescale : refit)).catch(() => undefined);

    const copySelection = (clear = true) => {
      const sel = term.getSelection();
      if (!sel) return false;
      void copyText(sel);
      if (clear) term.clearSelection();
      return true;
    };
    const pasteClipboard = async () => {
      if (mini) return;
      const text = await readClipboard();
      if (text) term.paste(text);
      else toast("Paste with Ctrl+Shift+V (the browser only lets the page read the clipboard on localhost or https)", { duration: 4000 });
    };
    term.attachCustomKeyEventHandler((ev) => {
      if (isAppCombo(ev)) return false;  // the app's own keys (switch workspace, move focus, zoom) are not for the agent
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
    const onFocusIn = () => { if (!handing.current) onFocusRef.current?.(); };
    if (!mini) {
      el.addEventListener("mouseup", onMouseUp);
      el.addEventListener("contextmenu", onContextMenu);
      el.addEventListener("focusin", onFocusIn);
    }

    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const q = mini ? "?mode=mini" : "";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/term/${tid}${q}`);
    ws.binaryType = "arraybuffer";
    const report = (s: Status, msg?: string) => {
      setStatus({ s, msg });
      onStatusRef.current?.(s, msg);
    };
    ws.onopen = () => {
      if (!mini) ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      report("open");
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        try {
          const m = JSON.parse(ev.data);
          if (m.type === "error") {
            if (!mini) term.write(`\r\n\x1b[33m${m.message}\x1b[0m\r\n`);
            report("error", m.message);
            return;
          }
          if (m.type === "size" && mini) {
            term.resize(Math.max(2, Number(m.cols) || 80), Math.max(1, Number(m.rows) || 24));
            requestAnimationFrame(rescale);
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
    const send = (d: string) => {
      if (!mini && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "input", data: d }));
    };
    const dataSub = term.onData(send);
    const binSub = term.onBinary(send);
    const resizeSub = term.onResize(({ cols, rows }) => {
      if (!mini && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "resize", cols, rows }));
    });
    const ro = new ResizeObserver(() => {
      if (mini) rescale();
      else refit();
    });
    ro.observe(box);
    return () => {
      gone = true;
      ro.disconnect();
      el.removeEventListener("mouseup", onMouseUp);
      el.removeEventListener("contextmenu", onContextMenu);
      el.removeEventListener("focusin", onFocusIn);
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
  }, [tid, mini, fontSize, dark]);

  useEffect(() => {
    if (!focused || mini) return;
    const ae = document.activeElement as HTMLElement | null;
    // someone typing into a field (an answer box, a dialog) keeps the keyboard
    const typing = !!ae && !ae.classList.contains("xterm-helper-textarea") && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA" || ae.isContentEditable || !!ae.closest("[role=dialog]"));
    if (typing) return;
    // The focus event this causes must not come back as a choice of yours: an effect of an earlier render can run
    // after a newer focus was set (two quick Ctrl+Shift+arrows), and would undo it.
    handing.current = true;
    try {
      termRef.current?.focus();
    } finally {
      handing.current = false;
    }
  }, [focused, mini, status.s]);

  return (
    <div ref={boxRef} data-term={mode} className={cn("group relative h-full w-full overflow-hidden", dark ? "bg-[#0b0e13]" : "bg-white", className)}
      onMouseDown={() => onFocus?.()}>
      <div ref={ref} className={mini ? "pointer-events-none absolute left-0 top-0 origin-top-left" : "h-full w-full"} onClick={() => !mini && termRef.current?.focus()} />
      {status.s !== "open" ? (
        <div className="pointer-events-none absolute inset-x-0 bottom-2 flex justify-center">
          <span className="rounded-full border border-border bg-surface/90 px-2.5 py-0.5 text-[11px] text-muted backdrop-blur">
            {status.s === "connecting" ? "connecting to the terminal" : status.msg || (status.s === "closed" ? "terminal closed" : "terminal error")}
          </span>
        </div>
      ) : null}
    </div>
  );
}
