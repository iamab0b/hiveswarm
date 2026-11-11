import { useEffect, useMemo, useState } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { api } from "@/lib/api";
import { useStore, useTask } from "@/lib/store";
import type { TaskDetail as TaskDetailData } from "@/lib/types";
import { agentAdapter, agentLabel, cn, elapsed, firstLine, flagAttention, fmtSecs, isTerminal, roleOf, sessionOf, taskAgent, untestedFlag } from "@/lib/utils";
import { AgentChip, EmptyState, SectionTitle, StepTimer } from "./bits";
import { StateBadge } from "./ui/badge";
import { Button } from "./ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/fields";
import { EventStream } from "./EventStream";
import { AttentionPanel } from "./Attention";
import { StandingOrders } from "./StandingOrders";
import { ConfirmDialog, ContinueDialog, RetryDialog } from "./dialogs";
import { act } from "./actions";

export function DiffView({ id }: { id: string }) {
  const [d, setD] = useState<{ ok: boolean; stat?: string; diff?: string; error?: string; branch?: string } | null>(null);
  useEffect(() => {
    api.diff(id).then(setD).catch((e) => setD({ ok: false, error: String(e.message) }));
  }, [id]);
  if (!d) return <div className="p-4 text-[12px] text-dim">loading diff</div>;
  if (!d.ok) return <div className="p-4 text-[12px] text-dim">{d.error || "no diff"}</div>;
  const files = splitDiff(d.diff || "");
  return (
    <div className="grid gap-3">
      <div className="mono whitespace-pre-wrap rounded-md border border-border bg-surface-2 px-3 py-2 text-muted">{(d.stat || "").trim()}</div>
      {files.map((f) => (
        <div key={f.name} className="overflow-hidden rounded-md border border-border">
          <div className="mono flex items-center gap-2 border-b border-border bg-surface-2 px-3 py-1.5">
            <span className="text-fg">{f.name}</span>
            <span className="text-success">+{f.add}</span>
            <span className="text-danger">−{f.del}</span>
          </div>
          <div className="mono max-h-[520px] overflow-auto bg-surface leading-[19px]">
            {f.lines.map((l, i) => (
              <div key={i} className={cn("flex", l.t === "+" && "bg-success/10", l.t === "-" && "bg-danger/10", l.t === "@" && "bg-surface-2 text-muted")}>
                <span className="num w-10 shrink-0 select-none pr-2 text-right text-dim">{l.old ?? ""}</span>
                <span className="num w-10 shrink-0 select-none pr-2 text-right text-dim">{l.new ?? ""}</span>
                <span className={cn("w-4 shrink-0 select-none", l.t === "+" && "text-success", l.t === "-" && "text-danger")}>{l.t === "@" ? "" : l.t}</span>
                <span className={cn("whitespace-pre-wrap break-all pr-3", l.t === "+" && "text-success", l.t === "-" && "text-danger")}>{l.s}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function splitDiff(diff: string) {
  const files: { name: string; add: number; del: number; lines: { t: string; s: string; old?: number; new?: number }[] }[] = [];
  let cur: (typeof files)[number] | null = null;
  let o = 0, n = 0;
  for (const raw of diff.split("\n")) {
    if (raw.startsWith("diff --git")) {
      const m = raw.match(/ b\/(.+)$/);
      cur = { name: m ? m[1] : raw, add: 0, del: 0, lines: [] };
      files.push(cur);
      continue;
    }
    if (!cur) continue;
    if (raw.startsWith("+++") || raw.startsWith("---") || raw.startsWith("index ") || raw.startsWith("new file") || raw.startsWith("deleted file") || raw.startsWith("similarity") || raw.startsWith("rename")) continue;
    if (raw.startsWith("@@")) {
      const m = raw.match(/@@ -(\d+)(?:,\d+)? \+(\d+)/);
      o = m ? Number(m[1]) : 0;
      n = m ? Number(m[2]) : 0;
      cur.lines.push({ t: "@", s: raw });
      continue;
    }
    if (raw.startsWith("+")) { cur.add++; cur.lines.push({ t: "+", s: raw.slice(1), new: n++ }); continue; }
    if (raw.startsWith("-")) { cur.del++; cur.lines.push({ t: "-", s: raw.slice(1), old: o++ }); continue; }
    if (raw.startsWith("\\")) continue;
    cur.lines.push({ t: " ", s: raw.slice(1), old: o++, new: n++ });
  }
  return files;
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[84px_1fr] gap-2">
      <span className="text-dim">{k}</span>
      <span className="min-w-0 text-fg">{v}</span>
    </div>
  );
}

const ROLE_TITLE = { chat: "Chat agent", lead: "Lead", session: "Agent", task: "Task" } as const;

/** Everything about one task or agent that its tile has no room for: what it was asked, its attempts, its events,
 *  its diff, its standing orders, and the actions on it. Opens over the workspace; the tile stays where it is. */
function Detail({ id, tab: firstTab, onClose }: { id: string; tab?: string; onClose: () => void }) {
  const t = useTask(id);
  const entries = useStore((s) => s.byTask[id]) || [];
  const agents = useStore((s) => s.agents);
  const [tab, setTab] = useState(firstTab || "overview");
  const [detail, setDetail] = useState<TaskDetailData | null>(null);
  const [retry, setRetry] = useState(false);
  const [cont, setCont] = useState(false);
  const [confirm, setConfirm] = useState<null | "cancel" | "delete" | "merge" | "finish">(null);
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const i = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(i);
  }, []);
  useEffect(() => {
    let alive = true;
    const load = () => api.task(id).then((d) => alive && setDetail(d)).catch(() => undefined);
    load();
    const i = setInterval(load, 4000);
    return () => { alive = false; clearInterval(i); };
  }, [id, t?.state]);
  const lastErr = useMemo(() => [...(detail?.attempts || [])].reverse().find((a) => a.verifier_log)?.verifier_log || "", [detail]);
  if (!t) return <EmptyState title="Not found" hint="It may have been deleted." action={<Button onClick={onClose}>Close</Button>} />;
  const role = roleOf(t);
  const sess = sessionOf(t);
  const isSession = t.kind === "session";
  // a finished task is claimed by nobody; the agent of its last attempt is who did it
  const agent = taskAgent(t) || detail?.attempts[detail.attempts.length - 1]?.agent || null;
  const adapter = agentAdapter(agent, agents.find((a) => a.agent_id === agent)?.provider);
  const ended = isTerminal(t.state);
  const c = detail?.classification;
  const att = !ended && isSession && role !== "chat" && sess.attention && sess.attention.kind !== "input" ? sess.attention : null;
  const flag = !ended ? flagAttention(t) : untestedFlag(t);
  const hasWork = role === "session" || role === "task";
  return (
    <div className="flex h-full min-h-0 flex-col" data-detail={id}>
      <div className="flex min-h-11 shrink-0 flex-wrap items-center gap-2 border-b border-border px-4 py-1.5">
        <span className="text-[13px] font-semibold">{ROLE_TITLE[role]}</span>
        <AgentChip agent={agent} provider={adapter} label={adapter ? agentLabel(adapter) : undefined} lead={role === "lead"} />
        <span className="mono text-muted">{t.id.slice(0, 8)}</span>
        <StateBadge state={t.state} live attention={!!(att || flag)} />
        {!isSession ? <span className="num text-meta">attempt {t.attempts}/{t.max_attempts}</span> : null}
        <span className="min-w-0 flex-1 truncate text-[13px] text-muted" title={t.spec}>{role === "chat" || role === "lead" ? t.project : firstLine(t.spec, 120)}</span>
        {!ended ? <StepTimer entries={entries} agent={agent} now={now} className="max-w-[280px]" /> : null}
        {isSession ? <span className="num hidden text-meta lg:inline">{elapsed(sess.started_at || t.updated_at, now)} · {sess.turns || 0} turns · {sess.asks || 0} asks · {sess.auto_approved || 0} auto-ok</span> : null}
        <div className="flex items-center gap-1">
          {!ended && role === "session" ? <Button size="sm" variant="primary" onClick={() => setConfirm("finish")}>Finish</Button> : null}
          {!ended && role === "session" ? <Button size="sm" variant="secondary" onClick={() => setCont(true)}>Continue on…</Button> : null}
          {t.state === "done" && hasWork ? <Button size="sm" variant="primary" onClick={() => setConfirm("merge")}>Merge</Button> : null}
          {ended && role !== "chat" ? <Button size="sm" variant="secondary" onClick={() => setRetry(true)}>{isSession ? "Reopen" : "Retry"}</Button> : null}
          {!ended ? <Button size="sm" variant="ghost" className="hover:text-danger" onClick={() => setConfirm("cancel")}>{isSession ? "Stop" : "Cancel"}</Button> : null}
          {ended ? <Button size="sm" variant="ghost" className="hover:text-danger" onClick={() => setConfirm("delete")}>Delete</Button> : null}
          <Button size="icon-sm" variant="ghost" className="text-muted" aria-label="close" onClick={onClose}><X className="h-4 w-4" /></Button>
        </div>
      </div>
      <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
        <div className="shrink-0 border-b border-border px-4 py-1.5">
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="log">Events <span className="num text-dim">{entries.length}</span></TabsTrigger>
            {hasWork ? <TabsTrigger value="diff">Diff</TabsTrigger> : null}
            {role !== "chat" ? <TabsTrigger value="orders">Standing orders</TabsTrigger> : null}
          </TabsList>
        </div>
        <TabsContent value="overview" className="min-h-0 flex-1 overflow-y-auto">
          <div className="grid gap-5 px-5 py-4 lg:grid-cols-[1fr_300px]">
            <div className="grid content-start gap-5">
              {att ? <AttentionPanel tid={id} att={att} onContinue={() => setCont(true)} onFinish={() => setConfirm("finish")} onCancel={() => setConfirm("cancel")} /> : null}
              {flag ? <AttentionPanel tid={id} att={flag} isSession={isSession} onCancel={() => setConfirm("cancel")} onContinue={flag.kind === "untested" ? () => setRetry(true) : isSession ? () => setCont(true) : undefined} /> : null}
              {role === "chat" ? (
                <div className="card p-4 text-[13px] leading-5 text-muted">
                  The chat agent of {t.project === "_home" ? "Home" : t.project}: a Claude Code session with the hiveswarm tools in their chat role. It reads the swarm, keeps the plan, pauses and briefs the lead; it never edits code.
                </div>
              ) : (
                <div>
                  <SectionTitle>{role === "lead" ? "Request" : "Spec"}</SectionTitle>
                  <div className="card whitespace-pre-wrap p-4 text-[13px] leading-5">{t.spec.trim()}</div>
                </div>
              )}
              {hasWork ? (
                <div>
                  <SectionTitle>Acceptance</SectionTitle>
                  <div className={cn("card mono p-3", t.acceptance ? "text-fg" : "text-dim")}>{t.acceptance || "none (soft verify)"}</div>
                </div>
              ) : null}
              {detail?.attempts.length && role !== "chat" ? (
                <div>
                  <SectionTitle>Attempts</SectionTitle>
                  <div className="card divide-y divide-border">
                    {detail.attempts.map((a) => (
                      <div key={a.id} className="px-3 py-2">
                        <div className="flex items-center gap-2 text-[12px]">
                          <AgentChip agent={a.agent} size="sm" />
                          <span className={cn("font-medium", a.outcome === "pass" ? "text-success" : a.outcome ? "text-danger" : "text-muted")}>{a.outcome || "running"}</span>
                          <span className="num text-dim">{Math.round(a.wall_seconds || 0)}s</span>
                          {a.tokens_in ? <span className="num text-dim">tok {a.tokens_in}/{a.tokens_out}</span> : null}
                          {a.steps ? <span className="num text-dim" title="tool steps · average seconds per step · slowest step">{a.steps} steps · avg {fmtSecs(a.step_avg_s)} · max {fmtSecs(a.step_max_s)}{a.slow_steps ? <span> · {a.slow_steps} slow</span> : null}</span> : null}
                          {a.diff_stat ? <span className="mono truncate text-dim">{firstLine(a.diff_stat, 60)}</span> : null}
                          {a.branch ? <span className="mono ml-auto text-dim">{a.branch}</span> : null}
                        </div>
                        {a.verifier_log ? <pre className="mono mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface-2 p-2 text-muted">{a.verifier_log.slice(-2500)}</pre> : null}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
            <div className="grid content-start gap-5">
              <div>
                <SectionTitle>Details</SectionTitle>
                <div className="card grid gap-1.5 p-3 text-[12px]">
                  <Row k="project" v={t.project === "_home" ? "none (Home)" : t.project} />
                  <Row k="agent" v={<span className="mono">{agent || "unassigned"}</span>} />
                  {isSession ? <Row k="terminal" v={sess.host ? <span className="mono">{sess.host}{sess.tmux ? ` ${sess.tmux}` : ""}</span> : "not started"} /> : null}
                  {isSession ? <Row k="permissions" v={sess.permission_mode || "auto"} /> : null}
                  {hasWork ? <Row k="base" v={<span className="mono">{t.base_ref}</span>} /> : null}
                  {t.worktree ? <Row k="worktree" v={<span className="mono break-all text-[11px]">{t.worktree}</span>} /> : null}
                  {t.state === "done" && hasWork ? <Row k="branch" v={<span className="mono">hiveswarm/{t.id}</span>} /> : null}
                  {sess.continued_in ? <Row k="continued in" v={<span className="mono">{sess.continued_in.slice(0, 8)}</span>} /> : null}
                </div>
              </div>
              {c && !isSession ? (
                <div>
                  <SectionTitle>Classification</SectionTitle>
                  <div className="card grid gap-1.5 p-3 text-[12px]">
                    <Row k="type" v={<>{c.task_type} <span className="text-dim">({c.type_conf.toFixed(2)})</span></>} />
                    <Row k="difficulty" v={<>{c.difficulty.toFixed(1)} <span className="text-dim">({c.difficulty_conf.toFixed(2)})</span></>} />
                    <Row k="multistep" v={(c.is_multistep || 0).toFixed(2)} />
                    <Row k="tools" v={(c.needs_tools || 0).toFixed(2)} />
                    <Row k="via" v={c.source} />
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </TabsContent>
        <TabsContent value="log" className="min-h-0 flex-1">
          <EventStream entries={entries} showAgent emptyText="no events yet" />
        </TabsContent>
        {hasWork ? (
          <TabsContent value="diff" className="min-h-0 flex-1 overflow-y-auto">
            <div className="px-5 py-4">
              {["done", "verifying", "failed"].includes(t.state) ? <DiffView id={id} /> : <div className="text-[12px] text-dim">No branch yet; the diff appears once an attempt has committed.</div>}
            </div>
          </TabsContent>
        ) : null}
        {role !== "chat" ? (
          <TabsContent value="orders" className="min-h-0 flex-1 overflow-y-auto">
            <div className="max-w-2xl px-5 py-4">
              <StandingOrders taskId={id} project={t.project} />
              <div className="mt-3 text-[12px] leading-[18px] text-dim">Orders sit at the top of the agent's prompt and are repeated on the cadence shown: Claude Code gets them mid-turn through hooks, other agents get them typed in between turns. With auditing on, a likely violation is flagged on the agent's tile.</div>
            </div>
          </TabsContent>
        ) : null}
      </Tabs>
      <RetryDialog task={t} lastError={lastErr} open={retry} onOpenChange={setRetry} />
      <ContinueDialog task={t} open={cont} onOpenChange={setCont} />
      <ConfirmDialog open={confirm === "finish"} onOpenChange={(o) => !o && setConfirm(null)} title={`Finish ${t.id.slice(0, 8)}?`} body="Its worktree is committed, pushed as a branch, and verified. The agent's terminal closes." confirmLabel="Finish" onConfirm={() => act.finish(id)} />
      <ConfirmDialog open={confirm === "cancel"} onOpenChange={(o) => !o && setConfirm(null)} title={`${isSession ? "Stop" : "Cancel"} ${t.id.slice(0, 8)}?`} body={isSession ? "This closes the agent's terminal. Uncommitted work in its worktree is kept on disk." : "This kills the running agent."} confirmLabel={isSession ? "Stop" : "Cancel task"} danger onConfirm={() => act.cancel(t)} />
      <ConfirmDialog open={confirm === "delete"} onOpenChange={(o) => !o && setConfirm(null)} title={`Delete ${t.id.slice(0, 8)}?`} body="Removes the record and its worktree. This cannot be undone." confirmLabel="Delete" danger onConfirm={async () => { await act.remove(id); onClose(); }} />
      <ConfirmDialog open={confirm === "merge"} onOpenChange={(o) => !o && setConfirm(null)} title={`Merge hiveswarm/${t.id.slice(0, 8)}?`} body={flag?.kind === "untested" ? `Into the project's current branch on the hub. This task is flagged untested: ${flag.summary}.` : "Into the project's current branch on the hub."} confirmLabel={flag?.kind === "untested" ? "Merge anyway" : "Merge"} onConfirm={() => act.merge(id, flag?.kind === "untested")} />
    </div>
  );
}

/** The one place a task's record opens: over whatever is on screen, closed with Esc. */
export function DetailOverlay() {
  const detail = useStore((s) => s.detail);
  const close = useStore((s) => s.closeDetail);
  return (
    <DialogPrimitive.Root open={!!detail} onOpenChange={(o) => !o && close()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <DialogPrimitive.Content aria-describedby={undefined} onOpenAutoFocus={(e) => { e.preventDefault(); (e.currentTarget as HTMLElement | null)?.focus(); }} tabIndex={-1}
          className="fixed left-1/2 top-1/2 z-50 flex h-[86vh] w-[min(1080px,94vw)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-border-strong bg-surface shadow-[var(--shadow-pop)] focus:outline-none">
          <DialogPrimitive.Title className="sr-only">Details</DialogPrimitive.Title>
          {detail ? <Detail key={detail.id} id={detail.id} tab={detail.tab} onClose={close} /> : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
