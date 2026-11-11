import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Crown, Maximize2, Minimize2, PanelRight, RotateCcw, X } from "lucide-react";
import { useStore } from "@/lib/store";
import type { Attention, Task } from "@/lib/types";
import { agentAdapter, agentLabel, attentionLabel, cn, firstLine, isTerminal, loudAttention, sessionOf, taskAgent, type TileRole } from "@/lib/utils";
import { AgentChip, AgentDot, StepTimer } from "./bits";
import { Button } from "./ui/button";
import { Tip } from "./ui/fields";
import { AttentionPanel } from "./Attention";
import { EventStream } from "./EventStream";
import { Terminal } from "./Terminal";
import { act } from "./actions";

/** A terminal smaller than this is shown as a live miniature instead of resizing the agent's screen to a sliver. */
const ATTACH_MIN = { w: 360, h: 160 };
const ATTACH_KEEP = { w: 330, h: 140 };

export function useSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => setSize((s) => (s.w === el.clientWidth && s.h === el.clientHeight ? s : { w: el.clientWidth, h: el.clientHeight }));
    read();
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size] as const;
}

const ROLE_WORD: Record<TileRole, string> = { chat: "chat", lead: "lead", session: "agent", task: "task" };

function StateText({ t, att }: { t: Task; att: Attention | null }) {
  const sess = sessionOf(t);
  if (att) {
    return (
      <span className="flex min-w-0 items-center gap-1.5 text-[12px] font-medium text-accent" data-needs-you>
        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" /><span className="truncate">{attentionLabel(att)}</span>
      </span>
    );
  }
  const word = t.kind === "session"
    ? (!sess.tmux ? (t.state === "running" || t.state === "claimed" ? "starting" : "waiting for a lane") : sess.turn === "working" ? "working" : sess.turn === "idle" ? "idle" : sess.turn || t.state)
    : t.state;
  const live = word === "working" || word === "running" || word === "verifying";
  return (
    <span className="flex min-w-0 items-center gap-1.5 text-[12px] text-muted">
      <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", live ? "bg-fg live-dot" : "bg-dim")} /><span className="truncate">{word}</span>
    </span>
  );
}

export interface TileProps {
  t: Task;
  role: TileRole;
  focused: boolean;
  zoomed: boolean;
  collapsed?: boolean;
  now: number;
  /** what the chat tile is the chat of: a project's name, or Home */
  where?: string;
  onFocus: () => void;
  onZoom: () => void;
  onContinue: () => void;
  onStop: () => void;
  /** Home chat only: close this chat agent and start a fresh one */
  onFresh?: () => void;
}

/** One agent in a workspace: a title line, what it needs from you if anything, and its terminal or its output. */
export function Tile({ t, role, focused, zoomed, collapsed, now, where, onFocus, onZoom, onContinue, onStop, onFresh }: TileProps) {
  const local = useStore((s) => s.local);
  const theme = useStore((s) => s.theme);
  const agents = useStore((s) => s.agents);
  const entries = useStore((s) => s.byTask[t.id]);
  const openDetail = useStore((s) => s.openDetail);
  const [bodyRef, size] = useSize<HTMLDivElement>();
  const [attach, setAttach] = useState(true);
  const sess = sessionOf(t);
  const att = role === "chat" ? null : loudAttention(t);
  const agent = taskAgent(t);
  const row = agents.find((a) => a.agent_id === agent);
  const adapter = agentAdapter(agent, row?.provider);
  const model = role === "chat" ? sess.model : row?.model;
  const effort = role === "chat" ? sess.effort : row?.effort;
  const ended = isTerminal(t.state);
  const isSession = t.kind === "session";
  const here = isSession && !!sess.tmux && !!sess.host && sess.host === local?.hostname && !ended;
  const remote = isSession && !!sess.host && !!local && sess.host !== local.hostname;

  // The chat agent and the lead are what you type into, so theirs is always the real terminal. An agent's becomes
  // a miniature when its tile is too small to work in (with some slack, so a drag across the line does not flicker).
  const always = role === "chat" || role === "lead" || zoomed;
  useEffect(() => {
    if (always) { setAttach(true); return; }
    if (!size.w || !size.h) return;
    setAttach((cur) => (cur ? size.w >= ATTACH_KEEP.w && size.h >= ATTACH_KEEP.h : size.w >= ATTACH_MIN.w && size.h >= ATTACH_MIN.h));
  }, [size.w, size.h, always]);

  const title = role === "chat" ? (where || "Home") : role === "lead" ? t.project : firstLine(t.spec, 120);
  const log = (entries || []).filter((e) => !e.source.endsWith(":think"));

  return (
    <div data-tile={t.id} data-role={role} data-focused={focused ? "true" : "false"} data-zoomed={zoomed ? "true" : "false"}
      className={cn("@container flex min-h-0 min-w-0 flex-col overflow-hidden bg-surface", zoomed ? "absolute inset-0 z-20" : "h-full")}
      onMouseDownCapture={onFocus}>
      <div className={cn("flex h-7 shrink-0 items-center gap-2 border-b border-border px-2", focused ? "bg-surface-2" : "bg-surface")}
        onDoubleClick={onZoom}>
        <span className={cn("flex shrink-0 items-center gap-1 text-[12px] font-medium", focused ? "text-fg" : "text-muted")}>
          {role === "lead" ? <Crown className="h-3 w-3" /> : null}{ROLE_WORD[role]}
        </span>
        <AgentChip agent={agent} provider={adapter} label={adapter ? agentLabel(adapter) : undefined} size="sm" className="shrink-0 @max-[380px]:hidden" />
        <span className="hidden shrink-0 items-center @max-[380px]:inline-flex @max-[150px]:hidden" title={agentLabel(adapter || agent)}><AgentDot agent={agent} provider={adapter} /></span>
        {model || effort ? <span className="mono shrink-0 text-[11px] text-dim @max-[460px]:hidden">{[model, effort].filter(Boolean).join(" · ")}</span> : null}
        <span className={cn("min-w-0 flex-1 basis-0 truncate text-[12px]", focused ? "text-fg" : "text-muted")} title={role === "chat" || role === "lead" ? undefined : t.spec}>{title}</span>
        {!ended && role !== "chat" ? <StepTimer entries={entries || []} agent={agent} now={now} compact className="shrink-0 @max-[520px]:hidden" /> : null}
        {role === "chat" && !sess.tmux ? <StateText t={t} att={null} /> : role !== "chat" ? <StateText t={t} att={att} /> : null}
        <div className="flex shrink-0 items-center">
          {onFresh ? <Tip label="Start a fresh chat"><Button size="icon-xs" variant="ghost" className="text-dim hover:text-fg" aria-label="fresh chat" onClick={onFresh}><RotateCcw className="h-3 w-3" /></Button></Tip> : null}
          <Tip label={zoomed ? "Back to the tiles · Ctrl+Shift+Z" : "Zoom · Ctrl+Shift+Z"}>
            <Button size="icon-xs" variant="ghost" className="text-dim hover:text-fg" aria-label="zoom" onClick={onZoom}>{zoomed ? <Minimize2 className="h-3 w-3" /> : <Maximize2 className="h-3 w-3" />}</Button>
          </Tip>
          <Tip label="Details · Ctrl+Shift+D">
            <Button size="icon-xs" variant="ghost" className="text-dim hover:text-fg @max-[200px]:hidden" aria-label="details" onClick={() => openDetail(t.id)}><PanelRight className="h-3 w-3" /></Button>
          </Tip>
          <Tip label={role === "chat" ? "Close this chat agent" : isSession ? "Stop this agent" : "Cancel this task"}>
            <Button size="icon-xs" variant="ghost" className="text-dim hover:text-danger" aria-label="stop" onClick={onStop}><X className="h-3 w-3" /></Button>
          </Tip>
        </div>
      </div>
      {att && !collapsed ? (
        <div className="shrink-0 border-b border-border bg-surface px-2 py-2">
          <AttentionPanel tid={t.id} att={att} compact isSession={isSession} onContinue={onContinue} onFinish={() => act.finish(t.id)} onCancel={onStop} />
        </div>
      ) : null}
      <div ref={bodyRef} className={cn("relative min-h-0 flex-1", collapsed && "hidden")} onDoubleClick={() => { if (here && !attach) onZoom(); }}>
        {here ? (
          <>
            <Terminal key={attach ? "attach" : "mini"} tid={t.id} mode={attach ? "attach" : "mini"} dark={theme === "dark"} focused={focused} onFocus={onFocus} />
            {!attach ? <div className="pointer-events-none absolute bottom-1.5 right-2 rounded-sm border border-border bg-surface/90 px-1.5 text-[10.5px] leading-4 text-dim">{focused ? "live · Ctrl+Shift+Z opens it" : "live · double-click to open"}</div> : null}
          </>
        ) : isSession && !sess.tmux && !ended ? (
          <div className="flex h-full flex-col items-center justify-center gap-1 px-6 text-center">
            <div className="text-[13px] font-medium text-fg">{role === "chat" ? "Starting the chat agent" : role === "lead" ? "Starting the lead" : "Starting"}</div>
            <div className="max-w-sm text-[12px] leading-[18px] text-muted">
              {t.state === "running" || t.state === "claimed" ? "Claude Code is opening in its terminal." : `Waiting for a free lane on ${agentLabel(adapter || agent)}${row ? "" : " (its worker has not registered it yet)"}.`}
            </div>
          </div>
        ) : (
          <div className="flex h-full min-h-0 flex-col">
            {remote ? <div className="shrink-0 border-b border-border px-2 py-1 text-meta">its terminal is on {sess.host}; this is its output</div> : null}
            <div className="min-h-0 flex-1"><EventStream entries={log} dense quiet emptyText="waiting for the first event" /></div>
          </div>
        )}
      </div>
    </div>
  );
}
