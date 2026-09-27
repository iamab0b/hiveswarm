import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "motion/react";
import { ArrowUpRight, GitMerge, Hexagon, LayoutGrid, Monitor, RotateCcw, Sparkles, TerminalSquare } from "lucide-react";
import { useStore } from "@/lib/store";
import type { Task } from "@/lib/types";
import { age, agentColor, agentLabel, cn, elapsed, firstLine, isLive, isTerminal, liveAttention, sessionOf, short, taskAgent } from "@/lib/utils";
import { AgentChip, EmptyState, StepTimer, TaskKindIcon, taskHref } from "@/components/bits";
import { Badge, StateBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AttentionPanel } from "@/components/Attention";
import { EventLine } from "@/components/EventStream";
import { Terminal } from "@/components/Terminal";
import { ContinueDialog, RetryDialog } from "@/components/dialogs";
import { act } from "@/components/actions";

function useNow() {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const i = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(i);
  }, []);
  return now;
}

function LiveCard({ t, wall, now, onContinue }: { t: Task; wall: boolean; now: number; onContinue: (id: string) => void }) {
  const byTask = useStore((s) => s.byTask);
  const local = useStore((s) => s.local);
  const theme = useStore((s) => s.theme);
  const nav = useNavigate();
  const sess = sessionOf(t);
  const att = liveAttention(t);
  const agent = taskAgent(t);
  const entries = (byTask[t.id] || []).filter((e) => !e.source.endsWith(":out") && !e.source.endsWith(":think")).slice(-3);
  const started = sess.started_at || t.updated_at;
  const color = agentColor(agent);
  const hasTerm = t.kind === "session" && !!sess.tmux && !!sess.host && sess.host === local?.hostname && !isTerminal(t.state);
  const tone = att?.kind === "permission" ? "danger-glow" : att ? "attention-glow" : "";
  return (
    <motion.div layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.98 }} transition={{ type: "spring", stiffness: 380, damping: 32 }}
      className={cn("card flex min-h-[220px] flex-col overflow-hidden", tone)}>
      <div className="flex items-center gap-2 border-b border-border px-3 py-2" style={{ boxShadow: `inset 3px 0 0 0 ${color}` }}>
        <TaskKindIcon t={t} />
        <AgentChip agent={agent} size="sm" lead={!!sess.lead} session={t.kind === "session"} />
        <Link to={taskHref(t)} className="mono text-[12px] text-muted hover:text-fg">{short(t.id)}</Link>
        <StateBadge state={t.state} live />
        {t.kind === "session" && sess.turn ? <Badge tone={sess.turn === "waiting" ? "warn" : sess.turn === "idle" ? "success" : "dim"}>{sess.turn}</Badge> : null}
        <span className="flex-1" />
        <StepTimer entries={byTask[t.id] || []} agent={agent} now={now} compact className="max-w-[140px]" />
        <span className="num text-[11px] text-dim">{elapsed(started, now)}</span>
        <Button size="icon-sm" variant="ghost" onClick={() => nav(taskHref(t))}><ArrowUpRight className="h-3.5 w-3.5" /></Button>
      </div>
      <div className="px-3 pt-2">
        <Link to={taskHref(t)} className="line-clamp-2 text-[13px] font-medium leading-snug text-fg hover:underline underline-offset-2" title={t.spec}>{firstLine(t.spec, 160)}</Link>
        <div className="mt-0.5 text-[11px] text-dim">{t.project}{t.acceptance ? " · verified by " : ""}{t.acceptance ? <span className="mono">{firstLine(t.acceptance, 40)}</span> : null}</div>
      </div>
      {att ? (
        <div className="px-3 pt-2">
          <AttentionPanel tid={t.id} att={att} compact isSession={t.kind === "session"} onContinue={() => onContinue(t.id)} onFinish={() => act.finish(t.id)} onCancel={() => act.cancel(t)} />
        </div>
      ) : null}
      {wall && hasTerm ? (
        <div className="mx-3 mt-2 h-[190px] overflow-hidden rounded-md border border-border">
          <Terminal tid={t.id} readOnly fontSize={10.5} dark={theme === "dark"} />
        </div>
      ) : (
        <div className="mt-2 min-h-[60px] flex-1 px-1 pb-1">
          {entries.length ? entries.map((e) => <EventLine key={e.id} e={e} dense />) : <div className="px-2 py-2 text-[12px] text-dim">waiting for the first event…</div>}
        </div>
      )}
      <div className="flex items-center gap-1 border-t border-border px-2 py-1.5">
        {t.kind === "session" ? (
          <Button size="xs" variant="ghost" onClick={() => nav(`/sessions/${t.id}`)} disabled={!hasTerm && !sess.tmux}><TerminalSquare className="h-3 w-3" /> terminal</Button>
        ) : (
          <Button size="xs" variant="ghost" onClick={() => nav(`/tasks/${t.id}?tab=log`)}>log</Button>
        )}
        <span className="flex-1" />
        {t.kind === "session" && !isTerminal(t.state) ? <Button size="xs" variant="ghost" onClick={() => act.finish(t.id)}>finish</Button> : null}
        {!isTerminal(t.state) ? <Button size="xs" variant="ghost" className="text-danger" onClick={() => act.cancel(t)}>cancel</Button> : null}
      </div>
    </motion.div>
  );
}

export default function Swarm() {
  const tasks = useStore((s) => s.tasks);
  const agents = useStore((s) => s.agents);
  const taskById = useStore((s) => s.taskById);
  const [wall, setWall] = useState(() => localStorage.getItem("hm.wall") === "1");
  const [cont, setCont] = useState<string | null>(null);
  const [retry, setRetry] = useState<string | null>(null);
  const now = useNow();
  useEffect(() => localStorage.setItem("hm.wall", wall ? "1" : "0"), [wall]);

  const live = useMemo(() => {
    const rank = (t: Task) => {
      const a = liveAttention(t);
      if (a?.kind === "permission") return 0;
      if (a?.kind === "question") return 1;
      if (a) return 2;
      if (t.kind === "session") return 3;
      return 4;
    };
    return tasks.filter(isLive).sort((a, b) => rank(a) - rank(b) || b.updated_at - a.updated_at);
  }, [tasks]);
  const queued = useMemo(() => tasks.filter((t) => ["pending", "classifying", "classified", "assigned"].includes(t.state) && t.kind !== "session"), [tasks]);
  const recent = useMemo(() => tasks.filter((t) => isTerminal(t.state)).slice(0, 8), [tasks]);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[1600px] px-6 py-5">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <h1 className="flex items-center gap-2 text-[18px] font-semibold tracking-tight"><Hexagon className="h-4.5 w-4.5 text-accent" /> Swarm</h1>
          <div className="flex flex-wrap items-center gap-2">
            {agents.map((a) => (
              <div key={a.agent_id} className="flex items-center gap-2 rounded-md border border-border bg-surface px-2 py-1">
                <span className={cn("h-1.5 w-1.5 rounded-full", a.alive ? "bg-success" : "bg-danger")} />
                <span className="text-[12px] font-medium" style={{ color: agentColor(a.agent_id) }}>{agentLabel(a.agent_id)}</span>
                <span className="flex items-center gap-[3px]">
                  {Array.from({ length: Math.max(1, a.capacity || 1) }).map((_, i) => (
                    <span key={i} className={cn("h-3 w-1.5 rounded-[2px]", i < (a.busy || 0) ? "bg-info" : "bg-surface-3")} />
                  ))}
                </span>
                <span className="num text-[11px] text-dim">{a.busy || 0}/{a.capacity || 1}</span>
              </div>
            ))}
            {!agents.length ? <span className="text-[12px] text-dim">no agents registered</span> : null}
          </div>
          <span className="flex-1" />
          <div className="flex items-center gap-1 rounded-md border border-border bg-surface p-0.5">
            <Button size="xs" variant={wall ? "ghost" : "secondary"} onClick={() => setWall(false)}><LayoutGrid className="h-3 w-3" /> cards</Button>
            <Button size="xs" variant={wall ? "secondary" : "ghost"} onClick={() => setWall(true)}><Monitor className="h-3 w-3" /> wall</Button>
          </div>
        </div>

        {live.length ? (
          <motion.div layout className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(380px, 1fr))" }}>
            <AnimatePresence initial={false}>
              {live.map((t) => <LiveCard key={t.id} t={t} wall={wall} now={now} onContinue={setCont} />)}
            </AnimatePresence>
          </motion.div>
        ) : (
          <div className="card">
            <EmptyState icon={<Sparkles className="h-8 w-8" />} title="No agents in flight" hint="Press g to describe a goal. Tasks run headless; Lead, Swarm and Session modes give you live terminals you can watch and steer." />
          </div>
        )}

        {queued.length ? (
          <div className="mt-6">
            <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wider text-dim">Queued · {queued.length}</div>
            <div className="card divide-y divide-border">
              {queued.map((t) => (
                <Link key={t.id} to={taskHref(t)} className="flex items-center gap-3 px-3 py-2 hover:bg-surface-2/60">
                  <StateBadge state={t.state} />
                  <span className="mono text-[12px] text-muted">{short(t.id)}</span>
                  <span className="min-w-0 flex-1 truncate text-[12.5px]">{firstLine(t.spec, 120)}</span>
                  <span className="text-[11px] text-dim">{t.project}</span>
                  <span className="text-[11px] text-dim">{age(t.updated_at, now)}</span>
                </Link>
              ))}
            </div>
          </div>
        ) : null}

        {recent.length ? (
          <div className="mt-6">
            <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wider text-dim">Recently finished</div>
            <div className="card divide-y divide-border">
              {recent.map((t) => (
                <div key={t.id} className="flex items-center gap-3 px-3 py-2">
                  <StateBadge state={t.state} />
                  <AgentChip agent={taskAgent(t)} size="sm" session={t.kind === "session"} lead={!!sessionOf(t).lead} />
                  <Link to={taskHref(t)} className="mono text-[12px] text-muted hover:text-fg">{short(t.id)}</Link>
                  <Link to={taskHref(t)} className="min-w-0 flex-1 truncate text-[12.5px] hover:underline underline-offset-2">{firstLine(t.spec, 110)}</Link>
                  <span className="text-[11px] text-dim">{age(t.updated_at, now)}</span>
                  {t.state === "done" ? <Button size="xs" variant="success" onClick={() => act.merge(t.id)}><GitMerge className="h-3 w-3" /> merge</Button> : null}
                  {t.state !== "done" ? <Button size="xs" variant="ghost" onClick={() => setRetry(t.id)}><RotateCcw className="h-3 w-3" /> retry</Button> : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
      <ContinueDialog task={cont ? taskById[cont] || null : null} open={!!cont} onOpenChange={(o) => !o && setCont(null)} />
      <RetryDialog task={retry ? taskById[retry] || null : null} open={!!retry} onOpenChange={(o) => !o && setRetry(null)} />
    </div>
  );
}
