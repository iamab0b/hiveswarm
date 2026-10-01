import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { api } from "@/lib/api";
import { useStore, useTask } from "@/lib/store";
import type { TaskDetail } from "@/lib/types";
import { cn, elapsed, firstLine, flagAttention, fmtSecs, isTerminal, taskAgent, untestedFlag } from "@/lib/utils";
import { AgentChip, EmptyState, SectionTitle, StepTimer } from "@/components/bits";
import { StateBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/fields";
import { EventStream } from "@/components/EventStream";
import { AttentionPanel } from "@/components/Attention";
import { StandingOrders } from "@/components/StandingOrders";
import { ConfirmDialog, RetryDialog } from "@/components/dialogs";
import { act } from "@/components/actions";

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
                <span className="w-10 shrink-0 select-none pr-2 text-right text-dim num">{l.old ?? ""}</span>
                <span className="w-10 shrink-0 select-none pr-2 text-right text-dim num">{l.new ?? ""}</span>
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

export default function TaskPage() {
  const { id = "" } = useParams();
  const t = useTask(id);
  const byTask = useStore((s) => s.byTask);
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "overview";
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [retry, setRetry] = useState(false);
  const [confirm, setConfirm] = useState<null | "cancel" | "delete" | "merge">(null);
  const [now, setNow] = useState(Date.now() / 1000);
  const entries = byTask[id] || [];
  useEffect(() => {
    const i = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(i);
  }, []);
  useEffect(() => {
    if (!id) return;
    let alive = true;
    const load = () => api.task(id).then((d) => alive && setDetail(d)).catch(() => undefined);
    load();
    const i = setInterval(load, 4000);
    return () => { alive = false; clearInterval(i); };
  }, [id, t?.state]);
  const lastErr = useMemo(() => [...(detail?.attempts || [])].reverse().find((a) => a.verifier_log)?.verifier_log || "", [detail]);
  if (!t) return <EmptyState title="Task not found" action={<Link to="/"><Button>Back to the swarm</Button></Link>} />;
  const agent = taskAgent(t);
  const ended = isTerminal(t.state);
  const c = detail?.classification;
  const flag = !ended ? flagAttention(t) : untestedFlag(t);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-11 shrink-0 flex-wrap items-center gap-2 border-b border-border bg-surface px-4 py-1.5">
        <Link to="/" className="rounded-md p-1 text-muted hover:bg-surface-2 hover:text-fg"><ArrowLeft className="h-4 w-4" /></Link>
        <AgentChip agent={agent} />
        <span className="mono text-muted">{t.id.slice(0, 8)}</span>
        <StateBadge state={t.state} live attention={!!flag} />
        <span className="num text-meta">attempt {t.attempts}/{t.max_attempts}</span>
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium" title={t.spec}>{firstLine(t.spec, 120)}</span>
        {!ended ? <StepTimer entries={entries} agent={agent} now={now} className="max-w-[320px]" /> : null}
        {!ended ? <span className="num text-meta">{elapsed(t.updated_at, now)}</span> : null}
        <div className="flex items-center gap-1">
          {t.state === "done" ? <Button size="sm" variant="primary" onClick={() => setConfirm("merge")}>Merge</Button> : null}
          {ended ? <Button size="sm" variant="secondary" onClick={() => setRetry(true)}>Retry</Button> : null}
          {!ended ? <Button size="sm" variant="ghost" className="hover:text-danger" onClick={() => setConfirm("cancel")}>Cancel</Button> : null}
          <Button size="sm" variant="ghost" className="hover:text-danger" onClick={() => setConfirm("delete")}>Delete</Button>
        </div>
      </div>
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: v })} className="flex min-h-0 flex-1 flex-col">
        <div className="shrink-0 border-b border-border bg-surface px-4 py-1.5">
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="log">Log <span className="num text-dim">{entries.length}</span></TabsTrigger>
            <TabsTrigger value="diff">Diff</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="overview" className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto grid max-w-5xl gap-5 px-6 py-5 lg:grid-cols-[1fr_320px]">
            <div className="grid gap-5">
              {flag ? <AttentionPanel tid={id} att={flag} isSession={false} onCancel={() => setConfirm("cancel")} onContinue={flag.kind === "untested" ? () => setRetry(true) : undefined} /> : null}
              <div>
                <SectionTitle>Spec</SectionTitle>
                <div className="card whitespace-pre-wrap p-4 text-[13px] leading-5">{t.spec.trim()}</div>
              </div>
              <div>
                <SectionTitle>Acceptance</SectionTitle>
                <div className={cn("card mono p-3", t.acceptance ? "text-fg" : "text-dim")}>{t.acceptance || "none (soft verify)"}</div>
              </div>
              {detail?.attempts.length ? (
                <div>
                  <SectionTitle>Attempts</SectionTitle>
                  <div className="card divide-y divide-border">
                    {detail.attempts.map((a) => (
                      <div key={a.id} className="px-3 py-2">
                        <div className="flex items-center gap-2 text-[12px]">
                          <AgentChip agent={a.agent} size="sm" />
                          <span className={cn("font-medium", a.outcome === "pass" ? "text-success" : a.outcome ? "text-danger" : "text-muted")}>{a.outcome || "running…"}</span>
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
                  <Row k="project" v={t.project} />
                  <Row k="base" v={<span className="mono">{t.base_ref}</span>} />
                  <Row k="kind" v={t.kind} />
                  {t.worktree ? <Row k="worktree" v={<span className="mono break-all text-[11px]">{t.worktree}</span>} /> : null}
                  {t.state === "done" ? <Row k="branch" v={<span className="mono">hiveswarm/{t.id}</span>} /> : null}
                </div>
              </div>
              <StandingOrders taskId={id} project={t.project} />
              {c ? (
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
          <EventStream entries={entries} showAgent emptyText="no log yet" />
        </TabsContent>
        <TabsContent value="diff" className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-6xl px-6 py-5">
            {["done", "verifying", "failed"].includes(t.state) ? <DiffView id={id} /> : <div className="text-[12px] text-dim">No branch yet; the diff appears once an attempt has committed.</div>}
          </div>
        </TabsContent>
      </Tabs>
      <RetryDialog task={t} lastError={lastErr} open={retry} onOpenChange={setRetry} />
      <ConfirmDialog open={confirm === "cancel"} onOpenChange={(o) => !o && setConfirm(null)} title={`Cancel ${t.id.slice(0, 8)}?`} body="This kills the running agent." confirmLabel="Cancel task" danger onConfirm={() => act.cancel(t)} />
      <ConfirmDialog open={confirm === "delete"} onOpenChange={(o) => !o && setConfirm(null)} title={`Delete ${t.id.slice(0, 8)}?`} body="Removes the record and its worktree. This cannot be undone." confirmLabel="Delete" danger onConfirm={() => act.remove(id)} />
      <ConfirmDialog open={confirm === "merge"} onOpenChange={(o) => !o && setConfirm(null)} title={`Merge hiveswarm/${t.id.slice(0, 8)}?`} body={flag?.kind === "untested" ? `Into the project's current branch on the hub. This task is flagged untested: ${flag.summary}.` : "Into the project's current branch on the hub."} confirmLabel={flag?.kind === "untested" ? "Merge anyway" : "Merge"} onConfirm={() => act.merge(id, flag?.kind === "untested")} />
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[84px_1fr] gap-2">
      <span className="text-dim">{k}</span>
      <span className="min-w-0 text-fg">{v}</span>
    </div>
  );
}
