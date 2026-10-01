import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { Search } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { Agent, AgentStatus, AgentTable, Profile, ProfilesInfo, RulesetRow, Task } from "@/lib/types";
import { age, agentColor, agentLabel, classifyEntry, cn, firstLine, fmtPct, fmtSecs, isLive, sessionOf, short, taskAgent } from "@/lib/utils";
import { AgentChip, EmptyState, PageHeader, StatusBadge, TaskKindIcon, taskHref } from "@/components/bits";
import { StateBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/fields";
import { EventStream } from "@/components/EventStream";
import { Profiles } from "@/components/Profiles";

const StatsCharts = lazy(() => import("./StatsCharts"));

export function TaskRedirect() {
  const { id = "" } = useParams();
  const t = useStore((s) => s.taskById[id]);
  const tasks = useStore((s) => s.tasks);
  if (!tasks.length) return <div className="p-6 text-[12px] text-dim">loading</div>;
  if (!t) return <Navigate to="/tasks" replace />;
  return <Navigate to={taskHref(t)} replace />;
}

function matches(t: Task, q: string): boolean {
  if (!q.trim()) return true;
  const terms = q.trim().split(/\s+/);
  const agent = taskAgent(t) || "";
  for (const term of terms) {
    const i = term.indexOf(":");
    if (i > 0) {
      const k = term.slice(0, i), v = term.slice(i + 1).toLowerCase();
      const val = ({ state: t.state, agent, project: t.project, kind: t.kind } as Record<string, string>)[k] || "";
      if (!val.toLowerCase().includes(v)) return false;
    } else {
      const hay = `${t.id} ${t.project} ${t.spec} ${agent}`.toLowerCase();
      if (!hay.includes(term.toLowerCase())) return false;
    }
  }
  return true;
}

const TH = "text-label px-3 py-2 text-left font-medium";

export function TasksPage() {
  const tasks = useStore((s) => s.tasks);
  const [q, setQ] = useState("");
  const rows = useMemo(() => tasks.filter((t) => matches(t, q)), [tasks, q]);
  const now = Date.now() / 1000;
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-6 py-5">
        <PageHeader
          title="Tasks"
          count={rows.length}
          right={(
            <div className="relative w-full max-w-md">
              <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-dim" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="state:running  agent:codex  project:demo  kind:session  or free text" className="pl-8" />
            </div>
          )}
        />
        {rows.length ? (
          <Card className="overflow-hidden">
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="border-b border-border">
                  <th className={TH}>id</th>
                  <th className={TH}>state</th>
                  <th className={cn(TH, "text-right")}>tries</th>
                  <th className={TH}>agent</th>
                  <th className={TH}>project</th>
                  <th className={cn(TH, "text-right")}>age</th>
                  <th className={TH}>spec</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => (
                  <tr key={t.id} className="h-9 border-b border-border last:border-0 hover:bg-surface-2">
                    <td className="px-3 py-1"><Link to={taskHref(t)} className="mono text-muted hover:text-fg">{short(t.id, 12)}</Link></td>
                    <td className="px-3 py-1"><StateBadge state={t.state} live={isLive(t)} /></td>
                    <td className="num px-3 py-1 text-right text-dim">{t.attempts}/{t.max_attempts}</td>
                    <td className="px-3 py-1"><span className="inline-flex items-center gap-1.5"><TaskKindIcon t={t} /><span className="h-1.5 w-1.5 rounded-full" style={{ background: agentColor(taskAgent(t)) }} />{taskAgent(t) ? agentLabel(taskAgent(t)) : <span className="text-dim">—</span>}</span></td>
                    <td className="px-3 py-1 text-muted">{t.project}</td>
                    <td className="num px-3 py-1 text-right text-dim">{age(t.updated_at, now)}</td>
                    <td className="max-w-[520px] px-3 py-1"><Link to={taskHref(t)} className="block truncate hover:underline underline-offset-2">{firstLine(t.spec, 140)}</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ) : (
          <Card><EmptyState title={q ? "Nothing matches" : "No tasks yet"} hint={q ? "Try a broader filter." : "Press g to start."} /></Card>
        )}
      </div>
    </div>
  );
}

export function HivePage() {
  const feed = useStore((s) => s.feed);
  const [q, setQ] = useState("");
  const [only, setOnly] = useState<"all" | "coord" | "you">("coord");
  const entries = useMemo(() => {
    let rows = feed;
    if (only === "coord") rows = rows.filter((e) => { const { kind } = classifyEntry(e.source, e.chunk); return !["out", "think", "result", "tool", "msg", "info"].includes(kind); });
    if (only === "you") rows = rows.filter((e) => { const { kind } = classifyEntry(e.source, e.chunk); return ["you", "ask", "warn"].includes(kind); });
    if (q.trim()) {
      const s = q.trim().toLowerCase();
      rows = rows.filter((e) => e.task_id.includes(s) || e.source.toLowerCase().includes(s) || e.chunk.toLowerCase().includes(s));
    }
    return rows;
  }, [feed, q, only]);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-11 shrink-0 items-center gap-3 border-b border-border bg-surface px-5">
        <h1 className="text-[13px] font-semibold">Hive</h1>
        <span className="hidden text-[12px] text-muted md:inline">how the agents coordinate: routing, handoffs, approvals, answers, verification, overlaps</span>
        <span className="flex-1" />
        <div className="flex items-center rounded-md bg-surface-2 p-0.5">
          {(["coord", "you", "all"] as const).map((k) => (
            <Button key={k} size="xs" variant="ghost" className={cn(only === k && "bg-surface text-fg shadow-sm")} onClick={() => setOnly(k)}>{k === "coord" ? "coordination" : k === "you" ? "asks and answers" : "everything"}</Button>
          ))}
        </div>
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="filter" className="h-7 w-56" />
      </div>
      <div className="min-h-0 flex-1">
        <EventStream entries={entries} showTask showAgent emptyText="The hive is quiet." />
      </div>
    </div>
  );
}

export function StatsPage() {
  const [table, setTable] = useState<AgentTable | null>(null);
  const [rulesets, setRulesets] = useState<RulesetRow[]>([]);
  const [err, setErr] = useState("");
  const [sort, setSort] = useState<"status" | "agent" | "type" | "wall" | "step">("status");
  useEffect(() => {
    let alive = true;
    const load = () => {
      api.agentStats().then((t) => alive && setTable(t)).catch((e) => alive && setErr(String(e.message || e)));
      api.rulesetStats().then((r) => alive && setRulesets(r.rows)).catch(() => undefined);
    };
    load();
    const i = setInterval(load, 15000);
    return () => { alive = false; clearInterval(i); };
  }, []);
  const order: Record<AgentStatus, number> = { failing: 0, slow: 1, unknown: 2, healthy: 3 };
  const cells = useMemo(() => {
    const c = [...(table?.cells || [])];
    c.sort((a, b) => {
      if (sort === "agent") return a.agent.localeCompare(b.agent) || a.task_type.localeCompare(b.task_type);
      if (sort === "type") return a.task_type.localeCompare(b.task_type) || a.agent.localeCompare(b.agent);
      if (sort === "wall") return (b.avg_wall_s || 0) - (a.avg_wall_s || 0);
      if (sort === "step") return (b.avg_step_s || 0) - (a.avg_step_s || 0);
      return order[a.status] - order[b.status] || a.agent.localeCompare(b.agent);
    });
    return c;
  }, [table, sort]);
  const agentsSorted = useMemo(() => [...(table?.agents || [])].sort((a, b) => a.agent.localeCompare(b.agent)), [table]);
  const th = (label: string, key?: typeof sort, right?: boolean) => (
    <th className={cn(TH, right ? "text-right" : "text-left", key && "cursor-pointer hover:text-fg", key === sort && "text-fg")} onClick={() => key && setSort(key)}>{label}</th>
  );
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-6 py-5">
        <PageHeader
          title="Stats"
          subtitle={<>How each agent does on each kind of work: pass rate, wall time and how long its tool steps take. A <b className="font-medium text-fg">slow</b> or <b className="font-medium text-fg">failing</b> agent is on probation for that kind of work: the router and the lead stop giving it hard tasks of that type but keep sending easy ones, so it can earn its way back. A step over {table?.slow_step_s ?? 120}s counts as slow.</>}
        />
        {err ? <Card className="p-4 text-[12px] text-danger">{err}</Card> : !table ? <div className="text-[12px] text-dim">loading</div> : !table.cells.length ? (
          <Card><EmptyState title="No finished attempts yet" hint="Numbers appear after the first verified attempts. Live step timers already show on the Swarm cards." /></Card>
        ) : (
          <>
            <div className="mb-6 grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
              {agentsSorted.map((a) => (
                <Card key={a.agent} className="p-4">
                  <div className="flex items-center justify-between gap-2">
                    <AgentChip agent={a.agent} />
                    <StatusBadge status={a.status} />
                  </div>
                  <div className="mt-4 grid grid-cols-3 gap-3">
                    <Stat label="pass" value={fmtPct(a.pass_rate)} tone={a.pass_rate === null ? undefined : a.pass_rate >= 0.7 ? "success" : a.pass_rate >= 0.4 ? undefined : "danger"} />
                    <Stat label="per run" value={fmtSecs(a.avg_wall_s)} />
                    <Stat label="per step" value={fmtSecs(a.avg_step_s)} />
                  </div>
                  <div className="mt-3 text-meta">{a.n} attempt{a.n === 1 ? "" : "s"} · {a.steps} steps{a.slow_steps ? ` · ${a.slow_steps} slow` : ""}</div>
                  {a.probation.length ? (
                    <div className="mt-2 border-l-2 border-l-accent pl-2 text-[12px] text-muted">
                      on probation for {a.probation.map((p) => `${p.task_type} (${p.status})`).join(", ")}
                    </div>
                  ) : null}
                </Card>
              ))}
            </div>
            <Suspense fallback={<div className="mb-6 h-[220px]" />}><StatsCharts agents={agentsSorted} /></Suspense>
            <Card className="overflow-x-auto">
              <table className="w-full min-w-[860px] border-collapse text-[13px]">
                <thead>
                  <tr className="border-b border-border">
                    {th("agent", "agent")}{th("work", "type")}{th("status", "status")}{th("n", undefined, true)}{th("pass", undefined, true)}{th("per attempt", "wall", true)}{th("per step", "step", true)}{th("p90 step", undefined, true)}{th("slow", undefined, true)}{th("trend", undefined, true)}{th("why")}
                  </tr>
                </thead>
                <tbody>
                  {cells.map((c) => (
                    <tr key={`${c.agent}-${c.task_type}`} className="h-9 border-b border-border last:border-0 hover:bg-surface-2">
                      <td className="px-3 py-1"><AgentChip agent={c.agent} size="sm" /></td>
                      <td className="px-3 py-1 text-muted">{c.task_type}</td>
                      <td className="px-3 py-1"><StatusBadge status={c.status} /></td>
                      <td className="num px-3 py-1 text-right">{c.n}</td>
                      <td className={cn("num px-3 py-1 text-right", c.pass_rate !== null && c.pass_rate < 0.4 && "text-danger")}>{fmtPct(c.pass_rate)}</td>
                      <td className="num px-3 py-1 text-right">{fmtSecs(c.avg_wall_s)}</td>
                      <td className="num px-3 py-1 text-right">{fmtSecs(c.avg_step_s)}</td>
                      <td className="num px-3 py-1 text-right text-muted">{fmtSecs(c.p90_step_s)}</td>
                      <td className="num px-3 py-1 text-right">{c.steps ? `${c.slow_steps}/${c.steps}` : "–"}</td>
                      <td className={cn("num px-3 py-1 text-right", c.trend !== null && c.trend >= 1.5 && "text-danger")}>{c.trend === null ? "–" : `${c.trend.toFixed(1)}×`}</td>
                      <td className="px-3 py-1 text-[12px] text-dim">{c.why}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
            <div className="mt-2 text-meta">per attempt = wall time from start to verified · per step = one tool call · slow = steps over {table.slow_step_s}s · trend = last few attempts vs the ones before (above 1 is getting slower)</div>
            {rulesets.length ? (
              <section className="mt-6">
                <div className="text-label mb-2">With and without the Craft ruleset</div>
                <Card className="overflow-x-auto">
                  <table className="w-full border-collapse text-[13px]">
                    <thead>
                      <tr className="border-b border-border">
                        <th className={TH}>ruleset</th><th className={cn(TH, "text-right")}>attempts</th><th className={cn(TH, "text-right")}>pass</th><th className={cn(TH, "text-right")}>lines changed</th><th className={cn(TH, "text-right")}>per attempt</th><th className={cn(TH, "text-right")}>untested</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rulesets.map((r) => (
                        <tr key={r.ruleset} className="h-9 border-b border-border last:border-0">
                          <td className="px-3 py-1">{r.ruleset}</td>
                          <td className="num px-3 py-1 text-right">{r.n}</td>
                          <td className="num px-3 py-1 text-right">{fmtPct(r.pass_rate)}</td>
                          <td className="num px-3 py-1 text-right">{r.avg_lines === null ? "–" : Math.round(r.avg_lines)}</td>
                          <td className="num px-3 py-1 text-right">{fmtSecs(r.avg_wall_s)}</td>
                          <td className="num px-3 py-1 text-right">{r.untested}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
                <div className="mt-2 text-meta">lines changed = insertions plus deletions per verified attempt · untested = attempts that changed code without touching or running a test</div>
              </section>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "success" | "danger" }) {
  return (
    <div>
      <div className={cn("num text-[18px] font-semibold leading-6", tone === "success" && "text-success", tone === "danger" && "text-danger")}>{value}</div>
      <div className="text-meta">{label}</div>
    </div>
  );
}

const MAX_LANES = 32;
const CROWDED_LANES = 6;

/** Lanes an agent is asked to run: an override from `hm agents --set` when one is set, else its worker.toml. */
function wantedLanes(a: Agent): number {
  return a.desired_capacity ?? a.configured ?? a.capacity ?? 1;
}

/** The +/− on an agent's card. One number per agent: `concurrency` in the worker's worker.toml. When that file is on
 *  this machine the control edits it (and the table below shows the same number); for a worker elsewhere it sets the
 *  daemon-side override that `hm agents --set` uses, and says so. Hub lanes have no control. */
function LaneControl({ a, providerLanes, profile, reloadProfiles }: { a: Agent; providerLanes: number; profile: Profile | null; reloadProfiles: () => Promise<void> }) {
  const [pending, setPending] = useState<number | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const editsFile = profile !== null;
  const wanted = pending === undefined ? wantedLanes(a) : pending ?? a.configured ?? a.capacity;
  const applying = pending !== undefined || (a.desired_capacity != null && a.desired_capacity !== a.capacity) || (editsFile && a.desired_capacity == null && profile.concurrency !== a.capacity);
  useEffect(() => {
    if (pending === undefined) return;
    if (editsFile ? a.capacity === (pending ?? a.configured) : (pending === null ? a.desired_capacity == null : a.desired_capacity === pending)) setPending(undefined);
  }, [a.capacity, a.configured, a.desired_capacity, editsFile, pending]);
  const set = (n: number | null) => {
    setError(null);
    setPending(n);
    const run = async () => {
      if (editsFile && n !== null) {
        if (a.desired_capacity != null) await api.setCapacity(a.agent_id, null);
        const r = await api.setProfile({ name: a.agent_id, concurrency: n });
        if (!r.ok) throw new Error(r.reason || "could not save");
        await reloadProfiles();
      } else {
        await api.setCapacity(a.agent_id, n);
      }
    };
    run().catch((e) => { setError(String(e.message || e)); setPending(undefined); });
  };
  const crowded = providerLanes > CROWDED_LANES;
  if (a.local) {
    return (
      <div className="mt-3 border-t border-border pt-3">
        <div className="flex items-center gap-2">
          <span className="text-label">Lanes</span>
          <span className="num text-meta">{a.busy || 0} of 1 busy</span>
        </div>
        <div className="mt-1 text-meta">one lane; {a.where || `[workers.${a.agent_id}] in config.toml on ${a.host}`}</div>
      </div>
    );
  }
  const overridden = a.desired_capacity != null;
  return (
    <div className="mt-3 border-t border-border pt-3">
      <div className="flex items-center gap-2">
        <span className="text-label">Lanes</span>
        <span className="num text-meta">{a.busy || 0} of {a.capacity ?? 1} busy</span>
        <div className="ml-auto flex items-center gap-1">
          <Button size="icon-sm" variant="secondary" aria-label="fewer lanes" disabled={wanted <= 0} onClick={() => set(wanted - 1)}>−</Button>
          <span className="num w-7 text-center text-[13px] font-semibold text-fg">{wanted}</span>
          <Button size="icon-sm" variant="secondary" aria-label="more lanes" disabled={wanted >= MAX_LANES} onClick={() => set(wanted + 1)}>+</Button>
        </div>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 text-meta">
        {applying ? <span className="text-accent">applying…</span> : wanted === 0 ? <span className="text-accent">paused</span> : null}
        {!applying && overridden ? <span>set with {editsFile ? "hm agents --set" : "the app"}; worker.toml{a.host ? ` on ${a.host}` : ""} says {a.configured ?? "?"}</span> : null}
        {!applying && !overridden ? <span>{editsFile ? "concurrency in worker.toml, same as the table below" : `worker.toml on ${a.host || "the worker"}; set from here until its file changes`}</span> : null}
        {overridden && !applying ? <button type="button" className="text-muted hover:text-fg hover:underline" onClick={() => set(null)}>back to worker.toml</button> : null}
        {error ? <span className="text-danger">{error}</span> : null}
      </div>
      {crowded ? <div className="mt-1 text-[12px] text-muted">{providerLanes} lanes share one {a.provider || a.agent_id} sign-in; its rate limits apply to all of them.</div> : null}
    </div>
  );
}

export function AgentsPage() {
  const agents = useStore((s) => s.agents);
  const tasks = useStore((s) => s.tasks);
  const now = Date.now() / 1000;
  const [profiles, setProfiles] = useState<ProfilesInfo | null>(null);
  const reloadProfiles = useCallback(() => api.profiles().then(setProfiles).catch(() => setProfiles({ ok: false, reason: "not reachable", profiles: [], adapters: [] })), []);
  useEffect(() => { reloadProfiles(); }, [reloadProfiles]);
  const profileOf = (a: Agent): Profile | null =>
    (profiles?.ok && a.host === profiles.host && profiles.profiles.find((p) => p.name === a.agent_id)) || null;
  const lanesByProvider = useMemo(() => {
    const m: Record<string, number> = {};
    for (const a of agents) {
      const p = a.provider || a.agent_id;
      m[p] = (m[p] || 0) + wantedLanes(a);
    }
    return m;
  }, [agents]);
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-6 py-5">
        <PageHeader title="Agents" count={agents.length || undefined} subtitle="Every agent a worker has registered, and the lanes the hub runs itself. An agent's lane count is concurrency in its worker.toml: change it here or in the table below; the worker applies it without a restart." />
        <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))" }}>
          {agents.map((a) => {
            const working = tasks.filter((t) => isLive(t) && taskAgent(t) === a.agent_id);
            return (
              <Card key={a.agent_id + a.host} className="p-4" data-agent-card={a.agent_id}>
                <div className="flex items-center gap-2">
                  <AgentChip agent={a.agent_id} />
                  {a.local ? <span className="text-meta">hub lane</span> : null}
                  <span className={cn("ml-auto flex items-center gap-1.5 text-[12px]", a.alive ? "text-muted" : "text-danger")}><span className={cn("h-1.5 w-1.5 rounded-full", a.alive ? "bg-success" : "bg-danger")} />{a.alive ? (a.local ? "up" : "alive") : (a.local ? "down" : "gone")}</span>
                </div>
                <div className="mt-3 grid grid-cols-[80px_1fr] gap-x-2 gap-y-1 text-[12px] text-muted">
                  <span>host</span><span className="mono text-fg">{a.host || "—"}</span>
                  {a.provider && (a.provider !== a.agent_id || a.model || a.effort) ? <><span>runs as</span><span className="text-fg">{agentLabel(a.provider)}{a.model ? <> · <span className="mono">{a.model}</span></> : null}{a.effort ? <> · effort {a.effort}</> : null}</span></> : null}
                  {a.local ? null : <><span>last seen</span><span className="text-fg">{age(a.last_seen, now)} ago</span></>}
                  <span>can do</span><span className="text-fg">{(a.capabilities || []).join(", ") || "—"}</span>
                </div>
                <LaneControl a={a} providerLanes={lanesByProvider[a.provider || a.agent_id] || 0} profile={profileOf(a)} reloadProfiles={reloadProfiles} />
                {working.length ? (
                  <div className="mt-3 grid gap-1">
                    {working.map((t) => (
                      <Link key={t.id} to={taskHref(t)} className="flex min-w-0 items-center gap-2 rounded-md bg-surface-2 px-2 py-1 text-[12px] hover:bg-surface-3">
                        <TaskKindIcon t={t} /><span className="mono shrink-0 text-muted">{short(t.id)}</span><span className="min-w-0 flex-1 truncate">{firstLine(t.spec, 60)}</span>
                        {sessionOf(t).attention ? <span className="shrink-0 whitespace-nowrap text-accent">needs you</span> : null}
                      </Link>
                    ))}
                  </div>
                ) : <div className="mt-3 text-[12px] text-dim">idle</div>}
              </Card>
            );
          })}
          {!agents.length ? <Card className="col-span-full"><EmptyState title="No agents registered" hint="Start hiveswarm-worker (hm up does it for you); hub-local lanes (a local model on the hub) are checked directly." /></Card> : null}
        </div>
        <Profiles info={profiles} reload={reloadProfiles} />
      </div>
    </div>
  );
}
