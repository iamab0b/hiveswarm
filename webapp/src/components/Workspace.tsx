import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "@/lib/store";
import type { Task } from "@/lib/types";
import { HOME, cn, firstLine, isTerminal, loudAttention, roleOf, sessionOf, short, untestedFlag, type TileRole } from "@/lib/utils";
import { Tile } from "./Tile";

/** Below this width the columns stack: chat on top, then the lead, then the agents. */
const STACK_BELOW = 720;

// ── what a workspace remembers about its layout (per project, in this browser) ──

interface Saved {
  /** column widths as fractions, for chat | lead and for chat | lead | agents */
  cols2?: number[];
  cols3?: number[];
  /** a tile's height share after its divider was dragged, by task id */
  flex?: Record<string, number>;
}

function load(key: string): Saved {
  try {
    return JSON.parse(localStorage.getItem(`hm.ws.${key}`) || "{}") as Saved;
  } catch {
    return {};
  }
}

function save(key: string, v: Saved) {
  try {
    localStorage.setItem(`hm.ws.${key}`, JSON.stringify(v));
  } catch {
    /* no storage */
  }
}

const DEFAULT_COLS2 = [0.44, 0.56];
const DEFAULT_COLS3 = [0.3, 0.37, 0.33];
const MIN_COL = 0.14;
const FULL_TILES = 8; // past this, the least important headless tiles shrink to their title line
const COLLAPSED_PX = 28;

/** How much of its column a tile takes before anyone drags a divider. An agent in its own terminal gets a full
 *  tile, headless work a small one; a tile that needs you grows, and so does the one you are looking at. */
function weightOf(t: Task, role: TileRole, focused: boolean): number {
  // headless work and an agent still waiting for a lane have little to show; a terminal needs room
  let w = role === "task" || !sessionOf(t).tmux ? 1.4 : 3;
  if (loudAttention(t)) w *= 1.7;
  if (focused) w = Math.max(w, 3.2);
  return w;
}

function Gutter({ dir, onDrag, onReset }: { dir: "col" | "row"; onDrag: (deltaPx: number, start: boolean) => void; onReset?: () => void }) {
  const last = useRef(0);
  return (
    <div role="separator" aria-orientation={dir === "col" ? "vertical" : "horizontal"} data-gutter={dir}
      className={cn("group relative z-10 shrink-0 bg-border", dir === "col" ? "w-px cursor-col-resize" : "h-px cursor-row-resize")}
      onDoubleClick={onReset}
      onPointerDown={(e) => {
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
        last.current = dir === "col" ? e.clientX : e.clientY;
        onDrag(0, true);
        e.preventDefault();
      }}
      onPointerMove={(e) => {
        if (!(e.currentTarget as HTMLElement).hasPointerCapture(e.pointerId)) return;
        const pos = dir === "col" ? e.clientX : e.clientY;
        const d = pos - last.current;
        if (d) onDrag(d, false);
        last.current = pos;
      }}
      onPointerUp={(e) => (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId)}>
      <span className={cn("absolute bg-transparent transition-colors duration-150 group-hover:bg-border-strong", dir === "col" ? "-left-[3px] top-0 h-full w-[7px]" : "-top-[3px] left-0 h-[7px] w-full")} />
    </div>
  );
}

export interface WorkspaceProps {
  /** null for Home */
  project: string | null;
  chat: Task | null;
  /** shown where the chat tile would be while there is none */
  chatPlaceholder?: React.ReactNode;
  lead: Task | null;
  /** shown in the lead's column while a project has no lead running */
  leadPlaceholder?: React.ReactNode;
  agents: Task[];
  done: Task[];
  queued: number;
  now: number;
  onStop: (t: Task) => void;
  onContinue: (t: Task) => void;
  onFreshChat?: () => void;
}

/**
 * A workspace: the chat agent, the project's lead beside it, and a tile for every agent the lead spawned. The first
 * two share most of the window; the rest stack in a third column (two side by side when there are many), each sized
 * by what it is. Dividers drag; a dragged size is kept. Finished work collapses into the strip at the bottom.
 */
export function Workspace({ project, chat, chatPlaceholder, lead, leadPlaceholder, agents, done, queued, now, onStop, onContinue, onFreshChat }: WorkspaceProps) {
  const key = project || HOME;
  const openDetail = useStore((s) => s.openDetail);
  const [saved, setSaved] = useState<Saved>(() => load(key));
  const [focused, setFocused] = useState<string | null>(null);
  const [zoomed, setZoomed] = useState<string | null>(null);
  const areaRef = useRef<HTMLDivElement>(null);
  const [areaW, setAreaW] = useState(0);
  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setAreaW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const stacked = areaW > 0 && areaW < STACK_BELOW;
  useEffect(() => { setSaved(load(key)); setZoomed(null); }, [key]);
  const update = useCallback((fn: (s: Saved) => Saved) => setSaved((s) => { const n = fn(s); save(key, n); return n; }), [key]);

  const ids = useMemo(() => [chat?.id, lead?.id, ...agents.map((a) => a.id)].filter(Boolean) as string[], [chat?.id, lead?.id, agents]);
  // The chat agent has the keyboard when a workspace opens (also when its tile only appears a moment later), until
  // you focus something yourself or start typing or clicking in the tile that has it: a tile that appears late
  // never takes the keyboard from under your hands. Asking for the chat agent back (the button where its tile
  // was) hands it the keyboard again. A tile that is gone gives the focus back.
  const chose = useRef(false);
  useEffect(() => { chose.current = false; }, [key]);
  const used = useCallback((e: React.KeyboardEvent | React.PointerEvent) => {
    if ("key" in e && ["Control", "Shift", "Alt", "Meta"].includes(e.key)) return;
    if ((e.target as HTMLElement | null)?.closest?.("[data-tile]")) chose.current = true;
  }, []);
  const wantChat = useCallback(() => { if (!chat) chose.current = false; }, [chat]);
  const focusedRef = useRef<string | null>(null);
  focusedRef.current = focused;
  // called when you focus a tile (a click, a key, tabbing into its terminal); the app handing a terminal the
  // keyboard does not come through here
  const focus = useCallback((id: string | null) => {
    if (id !== focusedRef.current) chose.current = true;
    focusedRef.current = id;  // two key presses can arrive before the next render
    setFocused(id);
  }, []);
  useEffect(() => {
    if (!chose.current && chat && focused !== chat.id) { setFocused(chat.id); return; }
    if (focused && ids.includes(focused)) return;
    setFocused(chat?.id || lead?.id || ids[0] || null);
  }, [ids, focused, chat, lead?.id]);
  useEffect(() => { if (zoomed && !ids.includes(zoomed)) setZoomed(null); }, [ids, zoomed]);

  // Ctrl+Shift+arrows / Z / D, forwarded by the shell
  useEffect(() => {
    const onKey = (ev: Event) => {
      const k = (ev as CustomEvent).detail as { kind: string; dir?: "left" | "right" | "up" | "down" };
      const focused = focusedRef.current;
      if (k.kind === "zoom") { setZoomed((z) => (z ? null : focused)); return; }
      if (k.kind === "detail") { if (focused) openDetail(focused); return; }
      if (k.kind !== "focus" || !areaRef.current) return;
      const tiles = Array.from(areaRef.current.querySelectorAll<HTMLElement>("[data-tile]"));
      const cur = tiles.find((el) => el.dataset.tile === focused);
      if (!cur) { if (tiles[0]) focus(tiles[0].dataset.tile || null); return; }
      const a = cur.getBoundingClientRect();
      const ax = a.left + a.width / 2, ay = a.top + a.height / 2;
      let best: { id: string; d: number } | null = null;
      for (const el of tiles) {
        if (el === cur) continue;
        const b = el.getBoundingClientRect();
        const bx = b.left + b.width / 2, by = b.top + b.height / 2;
        const dx = bx - ax, dy = by - ay;
        const ok = k.dir === "left" ? b.right <= a.left + 2 : k.dir === "right" ? b.left >= a.right - 2 : k.dir === "up" ? b.bottom <= a.top + 2 : b.top >= a.bottom - 2;
        if (!ok) continue;
        const d = k.dir === "left" || k.dir === "right" ? Math.abs(dx) + Math.abs(dy) * 2 : Math.abs(dy) + Math.abs(dx) * 2;
        if (!best || d < best.d) best = { id: el.dataset.tile || "", d };
      }
      if (best) { setZoomed(null); focus(best.id); }
    };
    window.addEventListener("hm:tile-key", onKey);
    return () => window.removeEventListener("hm:tile-key", onKey);
  }, [openDetail, focus]);

  // ── columns ──
  const hasLead = !!project && (!!lead || !!leadPlaceholder);
  const hasAgents = agents.length > 0;
  const ncols = 1 + (hasLead ? 1 : 0) + (hasAgents ? 1 : 0);
  const fr = ncols === 3 ? (saved.cols3?.length === 3 ? saved.cols3 : DEFAULT_COLS3) : ncols === 2 ? (saved.cols2?.length === 2 ? saved.cols2 : DEFAULT_COLS2) : [1];
  const drag = useRef<{ base: number[]; w: number; ha: number; hb: number; sum: number; acc: number }>({ base: [], w: 1, ha: 0, hb: 0, sum: 0, acc: 0 });
  const dragCols = (i: number) => (dx: number, start: boolean) => {
    const g = drag.current;
    if (start) { g.base = [...fr]; g.w = areaRef.current?.clientWidth || 1; g.acc = 0; return; }
    g.acc += dx;
    const d = g.acc / g.w;
    const a = g.base[i] + d, b = g.base[i + 1] - d;
    if (a < MIN_COL || b < MIN_COL) return;
    const cur = [...g.base];
    cur[i] = a; cur[i + 1] = b;
    update((s) => (ncols === 3 ? { ...s, cols3: cur } : { ...s, cols2: cur }));
  };
  const resetCols = () => update((s) => (ncols === 3 ? { ...s, cols3: undefined } : { ...s, cols2: undefined }));

  // ── the agents' column(s) ──
  const stacks = useMemo(() => {
    const items = agents.map((t) => {
      const role = roleOf(t);
      return { t, role, w: weightOf(t, role, t.id === focused), collapsed: false };
    });
    if (items.length > FULL_TILES) {
      // the quiet headless ones give way first, oldest first
      const quiet = items.filter((x) => x.role === "task" && !loudAttention(x.t) && x.t.id !== focused).sort((a, b) => a.t.created_at - b.t.created_at);
      for (const x of quiet.slice(0, items.length - FULL_TILES)) x.collapsed = true;
    }
    const open = items.filter((x) => !x.collapsed);
    const two = open.length > 4;
    const cols: (typeof items)[] = two ? [[], []] : [[]];
    const total = [0, 0];
    for (const x of items) {
      const c = two && total[1] < total[0] ? 1 : 0;
      cols[c].push(x);
      total[c] += x.collapsed ? 0.3 : x.w;
    }
    return cols;
  }, [agents, focused]);

  const dragRow = (above: string, below: string) => (dy: number, start: boolean) => {
    const g = drag.current;
    if (start) {
      const a = areaRef.current?.querySelector<HTMLElement>(`[data-slot="${above}"]`);
      const b = areaRef.current?.querySelector<HTMLElement>(`[data-slot="${below}"]`);
      if (!a || !b) { g.sum = 0; return; }
      g.ha = a.offsetHeight; g.hb = b.offsetHeight; g.acc = 0;
      g.sum = (Number(a.dataset.flex) || 1) + (Number(b.dataset.flex) || 1);
      return;
    }
    if (!g.sum) return;
    g.acc += dy;
    if (g.ha + g.acc < 56 || g.hb - g.acc < 56) return;
    const na = ((g.ha + g.acc) / (g.ha + g.hb)) * g.sum;
    update((s) => ({ ...s, flex: { ...(s.flex || {}), [above]: na, [below]: g.sum - na } }));
  };
  const resetRow = (above: string, below: string) => () => update((s) => {
    const f = { ...(s.flex || {}) };
    delete f[above];
    delete f[below];
    return { ...s, flex: f };
  });

  const tile = (t: Task, role: TileRole, collapsed = false) => (
    <Tile key={t.id} t={t} role={role} focused={focused === t.id} zoomed={zoomed === t.id} collapsed={collapsed && zoomed !== t.id} now={now}
      where={project || "Home"}
      onFocus={() => focus(t.id)}
      onZoom={() => { focus(t.id); setZoomed((z) => (z === t.id ? null : t.id)); }}
      onContinue={() => onContinue(t)} onStop={() => onStop(t)}
      onFresh={role === "chat" && !project ? onFreshChat : undefined} />
  );

  return (
    <div className="flex h-full min-h-0 flex-col" data-workspace={key}>
      <div ref={areaRef} className={cn("relative flex min-h-0 flex-1", stacked && "flex-col")} onKeyDownCapture={used} onPointerDownCapture={used}>
        <div className="min-h-0 min-w-0" style={{ flex: `${fr[0]} 1 0` }} data-col="chat" onPointerDownCapture={wantChat} onKeyDownCapture={wantChat}>
          {chat ? tile(chat, "chat") : chatPlaceholder}
        </div>
        {hasLead ? (
          <>
            {stacked ? <div className="h-px shrink-0 bg-border" /> : <Gutter dir="col" onDrag={dragCols(0)} onReset={resetCols} />}
            <div className="min-h-0 min-w-0" style={{ flex: `${fr[1]} 1 0` }} data-col="lead">
              {lead ? tile(lead, "lead") : leadPlaceholder}
            </div>
          </>
        ) : null}
        {hasAgents ? (
          <>
            {stacked ? <div className="h-px shrink-0 bg-border" /> : <Gutter dir="col" onDrag={dragCols(ncols - 2)} onReset={resetCols} />}
            <div className="flex min-h-0 min-w-0" style={{ flex: `${fr[ncols - 1]} 1 0` }} data-col="agents">
              {stacks.map((col, ci) => (
                <div key={ci} className={cn("flex min-h-0 min-w-0 flex-1 flex-col", ci > 0 && "border-l border-border")}>
                  {col.map((x, i) => {
                    const flex = x.collapsed ? 0 : saved.flex?.[x.t.id] ?? x.w;
                    const next = col[i + 1];
                    return (
                      <div key={x.t.id} className="contents">
                        <div data-slot={x.t.id} data-flex={flex} className="min-h-0 min-w-0"
                          style={x.collapsed ? { flex: `0 0 ${COLLAPSED_PX}px` } : { flex: `${flex} 1 0`, minHeight: COLLAPSED_PX }}>
                          {tile(x.t, x.role, x.collapsed)}
                        </div>
                        {next ? (
                          x.collapsed || next.collapsed
                            ? <div className="h-px shrink-0 bg-border" />
                            : <Gutter dir="row" onDrag={dragRow(x.t.id, next.t.id)} onReset={resetRow(x.t.id, next.t.id)} />
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </>
        ) : null}
      </div>
      {project && (done.length || queued) ? <DoneStrip done={done} queued={queued} /> : null}
    </div>
  );
}

/** Finished agents, as chips: the tile is gone, its record opens read-only. */
function DoneStrip({ done, queued }: { done: Task[]; queued: number }) {
  const openDetail = useStore((s) => s.openDetail);
  return (
    <div className="flex h-8 shrink-0 items-center gap-2 overflow-x-auto border-t border-border bg-surface px-2" data-done-strip>
      {queued ? <span className="shrink-0 whitespace-nowrap text-meta"><span className="num text-fg">{queued}</span> queued</span> : null}
      {done.length ? <span className="shrink-0 text-meta">finished</span> : null}
      {done.map((t) => {
        const role = roleOf(t);
        const untested = t.state === "done" && !!untestedFlag(t);
        return (
          <button key={t.id} type="button" onClick={() => openDetail(t.id)} title={t.spec} data-done-chip={t.id}
            className="flex h-5 max-w-[220px] shrink-0 items-center gap-1.5 rounded-sm border border-border bg-surface px-1.5 text-[11px] text-muted hover:bg-surface-2 hover:text-fg">
            <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", untested ? "bg-accent" : t.state === "done" ? "bg-success" : t.state === "failed" ? "bg-danger" : "bg-dim")} />
            <span className="mono text-[11px]">{short(t.id, 6)}</span>
            <span className="truncate">{role === "lead" ? "lead" : firstLine(t.spec, 40)}</span>
            {untested ? <span className="shrink-0 text-accent">untested</span> : null}
          </button>
        );
      })}
    </div>
  );
}

export function isDoneForStrip(t: Task): boolean {
  return isTerminal(t.state) && roleOf(t) !== "chat";
}
