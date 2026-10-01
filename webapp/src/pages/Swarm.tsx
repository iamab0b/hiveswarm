import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "motion/react";
import { ArrowUpRight } from "lucide-react";
import { useStore } from "@/lib/store";
import type { Task } from "@/lib/types";
import { age, agentLabel, cn, elapsed, firstLine, isLive, isTerminal, liveAttention, sessionOf, short, taskAgent } from "@/lib/utils";
import { AgentChip, AgentDot, EmptyState, PageHeader, StepTimer, TaskKindIcon, taskHref } from "@/components/bits";
import { StateBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
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
  const hasTerm = t.kind === "session" && !!sess.tmux && !!sess.host && sess.host === local?.hostname && !isTerminal(t.state);
  const turn = t.kind === "session" && sess.turn && sess.turn !== "working" ? sess.turn : null;
  return (
    <motion.div layout initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.15, ease: "easeOut" }}
      className="card flex min-h-[220px] flex-col overflow-hidden">
      <CardHeader className="px-3">
        <TaskKindIcon t={t} />
        <AgentChip agent={agent} size="sm" />
        <Link to={taskHref(t)} className="mono text-muted hover:text-fg">{short(t.id)}</Link>
        <StateBadge state={t.state} live attention={!!att} />
        {turn && !att ? <span className="text-meta">{turn}</span> : null}
        <span className="flex-1" />
        <StepTimer entries={byTask[t.id] || []} agent={agent} now={now} compact className="max-w-[140px]" />
        <span className="num text-meta">{elapsed(started, now)}</span>
        <Button size="icon-xs" variant="ghost" onClick={() => nav(taskHref(t))} aria-label="open"><ArrowUpRight className="h-3.5 w-3.5" /></Button>
      </CardHeader>
      <div className="px-4 pt-3">
        <Link to={taskHref(t)} className="line-clamp-2 text-[13px] font-medium leading-5 text-fg hover:underline underline-offset-2" title={t.spec}>{firstLine(t.spec, 160)}</Link>
        <div className="mt-0.5 text-meta">{t.project}{t.acceptance ? <> · verified by <span className="mono text-[11px]">{firstLine(t.acceptance, 40)}</span></> : null}</div>
      </div>
      {att ? (
        <div className="px-4 pt-3">
          <AttentionPanel tid={t.id} att={att} compact isSession={t.kind === "session"} onContinue={() => onContinue(t.id)} onFinish={() => act.finish(t.id)} onCancel={() => act.cancel(t)} />
        </div>
      ) : null}
      {wall && hasTerm ? (
        <div className="mx-4 mt-3 h-[190px] overflow-hidden rounded-md border border-border">
          <Terminal tid={t.id} readOnly fontSize={10.5} dark={theme === "dark"} />
        </div>
      ) : (
        <div className="mt-2 min-h-[60px] flex-1 px-2 pb-1">
          {entries.length ? entries.map((e) => <EventLine key={e.id} e={e} dense />) : <div className="px-2 py-2 text-[12px] text-dim">waiting for the first event</div>}
        </div>
      )}
      <div className="flex h-8 items-center gap-1 border-t border-border px-2">
        {t.kind === "session" ? (
          <Button size="xs" variant="ghost" onClick={() => nav(`/sessions/${t.id}`)} disabled={!hasTerm && !sess.tmux}>terminal</Button>
        ) : (
          <Button size="xs" variant="ghost" onClick={() => nav(`/tasks/${t.id}?tab=log`)}>log</Button>
        )}
        <span className="flex-1" />
        {t.kind === "session" && !isTerminal(t.state) ? <Button size="xs" variant="ghost" onClick={() => act.finish(t.id)}>finish</Button> : null}
        {!isTerminal(t.state) ? <Button size="xs" variant="ghost" className="hover:text-danger" onClick={() => act.cancel(t)}>cancel</Button> : null}
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
        <PageHeader
          title="Swarm"
          count={live.length || undefined}
          right={(
            <>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-muted">
                {agents.map((a) => (
                  <span key={a.agent_id} className="flex items-center gap-1.5">
                    <AgentDot agent={a.agent_id} className={cn(!a.alive && "opacity-40")} />
                    <span className={cn(a.alive ? "text-fg" : "text-dim line-through")}>{agentLabel(a.agent_id)}</span>
                    <span className="num text-dim">{a.busy || 0}/{a.capacity ?? 1}</span>
                  </span>
                ))}
                {!agents.length ? <span className="text-dim">no agents registered</span> : null}
              </div>
              <div className="flex items-center rounded-md bg-surface-2 p-0.5">
                <Button size="xs" variant="ghost" className={cn(!wall && "bg-surface text-fg shadow-sm")} onClick={() => setWall(false)}>cards</Button>
                <Button size="xs" variant="ghost" className={cn(wall && "bg-surface text-fg shadow-sm")} onClick={() => setWall(true)}>wall</Button>
              </div>
            </>
          )}
        />

        {live.length ? (
          <motion.div layout className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(380px, 1fr))" }}>
            <AnimatePresence initial={false}>
              {live.map((t) => <LiveCard key={t.id} t={t} wall={wall} now={now} onContinue={setCont} />)}
            </AnimatePresence>
          </motion.div>
        ) : (
          <Card>
            <EmptyState title="Nothing in flight" hint="Press g to describe a goal. Tasks run headless; Lead, Swarm and Session modes give you live terminals you can watch and steer." />
          </Card>
        )}

        {queued.length ? (
          <section className="mt-6">
            <div className="text-label mb-2">Queued <span className="num text-dim">{queued.length}</span></div>
            <Card className="divide-y divide-border">
              {queued.map((t) => (
                <Link key={t.id} to={taskHref(t)} className="row hover:bg-surface-2">
                  <StateBadge state={t.state} />
                  <span className="mono text-muted">{short(t.id)}</span>
                  <span className="min-w-0 flex-1 truncate text-[13px]">{firstLine(t.spec, 120)}</span>
                  <span className="text-meta">{t.project}</span>
                  <span className="num text-meta">{age(t.updated_at, now)}</span>
                </Link>
              ))}
            </Card>
          </section>
        ) : null}

        {recent.length ? (
          <section className="mt-6">
            <div className="text-label mb-2">Recently finished</div>
            <Card className="divide-y divide-border">
              {recent.map((t) => (
                <div key={t.id} className="row">
                  <StateBadge state={t.state} />
                  <AgentChip agent={taskAgent(t)} size="sm" session={t.kind === "session"} lead={!!sessionOf(t).lead} />
                  <Link to={taskHref(t)} className="mono text-muted hover:text-fg">{short(t.id)}</Link>
                  <Link to={taskHref(t)} className="min-w-0 flex-1 truncate text-[13px] hover:underline underline-offset-2">{firstLine(t.spec, 110)}</Link>
                  <span className="num text-meta">{age(t.updated_at, now)}</span>
                  {t.state === "done" ? <Button size="xs" variant="secondary" onClick={() => act.merge(t.id)}>merge</Button> : null}
                  {t.state !== "done" ? <Button size="xs" variant="ghost" onClick={() => setRetry(t.id)}>retry</Button> : null}
                </div>
              ))}
            </Card>
          </section>
        ) : null}
      </div>
      <ContinueDialog task={cont ? taskById[cont] || null : null} open={!!cont} onOpenChange={(o) => !o && setCont(null)} />
      <RetryDialog task={retry ? taskById[retry] || null : null} open={!!retry} onOpenChange={(o) => !o && setRetry(null)} />
    </div>
  );
}
