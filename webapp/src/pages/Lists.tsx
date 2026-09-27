import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { Activity, Bot, ListChecks, BarChart3, Filter } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { AgentStatus, AgentTable, Task } from "@/lib/types";
import { age, agentColor, agentLabel, classifyEntry, cn, firstLine, fmtPct, fmtSecs, isLive, sessionOf, short, taskAgent } from "@/lib/utils";
import { AgentChip, EmptyState, StatusBadge, TaskKindIcon, taskHref } from "@/components/bits";
import { StateBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/fields";
import { EventStream } from "@/components/EventStream";

export function TaskRedirect() {
  const { id = "" } = useParams();
  const t = useStore((s) => s.taskById[id]);
  const tasks = useStore((s) => s.tasks);
  if (!tasks.length) return <div className="p-6 text-[12.5px] text-dim">loading…</div>;
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

export function TasksPage() {
  const tasks = useStore((s) => s.tasks);
  const [q, setQ] = useState("");
  const rows = useMemo(() => tasks.filter((t) => matches(t, q)), [tasks, q]);
  const now = Date.now() / 1000;
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-6 py-5">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <h1 className="flex items-center gap-2 text-[18px] font-semibold tracking-tight"><ListChecks className="h-4.5 w-4.5 text-accent" /> Tasks <span className="text-[13px] font-normal text-dim">{rows.length}</span></h1>
          <div className="relative ml-auto w-full max-w-md">
            <Filter className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-dim" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="filter: state:running  agent:codex  project:demo  kind:session  or free text" className="pl-8" />
          </div>
        </div>
        {rows.length ? (
          <div className="card overflow-hidden">
            <table className="w-full border-collapse text-[12.5px]">
              <thead>
                <tr className="border-b border-border text-left text-[11px] uppercase tracking-wider text-dim">
                  <th className="px-3 py-2 font-semibold">id</th>
                  <th className="px-2 py-2 font-semibold">state</th>
                  <th className="px-2 py-2 font-semibold">att</th>
                  <th className="px-2 py-2 font-semibold">agent</th>
                  <th className="px-2 py-2 font-semibold">project</th>
                  <th className="px-2 py-2 font-semibold">age</th>
                  <th className="px-2 py-2 font-semibold">spec</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => (
                  <tr key={t.id} className={cn("border-b border-border last:border-0 hover:bg-surface-2/60", isLive(t) && "bg-surface-2/30")}>
                    <td className="px-3 py-1.5"><Link to={taskHref(t)} className="mono text-muted hover:text-fg">{short(t.id, 12)}</Link></td>
                    <td className="px-2 py-1.5"><StateBadge state={t.state} /></td>
                    <td className="num px-2 py-1.5 text-dim">{t.attempts}/{t.max_attempts}</td>
                    <td className="px-2 py-1.5"><span className="inline-flex items-center gap-1.5"><TaskKindIcon t={t} /><span style={{ color: agentColor(taskAgent(t)) }}>{taskAgent(t) ? agentLabel(taskAgent(t)) : "—"}</span></span></td>
                    <td className="px-2 py-1.5 text-muted">{t.project}</td>
                    <td className="num px-2 py-1.5 text-dim">{age(t.updated_at, now)}</td>
                    <td className="max-w-[520px] px-2 py-1.5"><Link to={taskHref(t)} className="block truncate hover:underline underline-offset-2">{firstLine(t.spec, 140)}</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="card"><EmptyState title={q ? "Nothing matches" : "No tasks yet"} hint={q ? "Try a broader filter." : "Press g to start."} /></div>
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
    if (only === "coord") rows = rows.filter((e) => { const { kind } = classifyEntry(e.source, e.chunk); return !["out", "think", "result", "tool", "msg", "info"].includes(kind) || kind === "msg" && false; });
    if (only === "you") rows = rows.filter((e) => { const { kind } = classifyEntry(e.source, e.chunk); return ["you", "ask", "warn"].includes(kind); });
    if (q.trim()) {
      const s = q.trim().toLowerCase();
      rows = rows.filter((e) => e.task_id.includes(s) || e.source.toLowerCase().includes(s) || e.chunk.toLowerCase().includes(s));
    }
    return rows;
  }, [feed, q, only]);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border bg-surface px-5 py-2.5">
        <h1 className="flex items-center gap-2 text-[15px] font-semibold tracking-tight"><Activity className="h-4 w-4 text-accent" /> Hive</h1>
        <span className="text-[12px] text-dim">how the agents coordinate: routing, handoffs, approvals, answers, verification, overlaps</span>
        <span className="flex-1" />
        <div className="flex items-center gap-1 rounded-md border border-border bg-surface-2 p-0.5">
          {(["coord", "you", "all"] as const).map((k) => (
            <Button key={k} size="xs" variant={only === k ? "secondary" : "ghost"} onClick={() => setOnly(k)}>{k === "coord" ? "coordination" : k === "you" ? "asks & answers" : "everything"}</Button>
          ))}
        </div>
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="filter…" className="w-56" />
      </div>
      <div className="min-h-0 flex-1">
        <EventStream entries={entries} showTask showAgent emptyText="the hive is quiet" />
      </div>
    </div>
  );
}

export function StatsPage() {
  const [table, setTable] = useState<AgentTable | null>(null);
  const [err, setErr] = useState("");
  const [sort, setSort] = useState<"status" | "agent" | "type" | "wall" | "step">("status");
  useEffect(() => {
    let alive = true;
    const load = () => api.agentStats().then((t) => alive && setTable(t)).catch((e) => alive && setErr(String(e.message || e)));
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
  const th = (label: string, key?: typeof sort, right?: boolean) => (
    <th className={cn("px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-dim", right ? "text-right" : "text-left", key && "cursor-pointer hover:text-fg", key === sort && "text-fg")} onClick={() => key && setSort(key)}>{label}</th>
  );
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-6 py-5">
        <div className="mb-4">
          <h1 className="flex items-center gap-2 text-[18px] font-semibold tracking-tight"><BarChart3 className="h-4.5 w-4.5 text-accent" /> Agent performance</h1>
          <p className="mt-0.5 max-w-3xl text-[12.5px] text-muted">How each agent does on each kind of work: pass rate, wall time, and how long its tool steps take. A <b className="text-warn">slow</b> or <b className="text-danger">failing</b> agent is on probation for that kind of work — the router and the lead stop giving it hard tasks of that type but keep sending easy ones, so it can earn its way back. A step over {table?.slow_step_s ?? 120}s counts as slow.</p>
        </div>
        {err ? <div className="card p-4 text-[12.5px] text-danger">{err}</div> : !table ? <div className="text-dim">loading…</div> : !table.cells.length ? (
          <div className="card"><EmptyState title="No finished attempts yet" hint="Numbers appear after the first verified attempts. Live step timers already show on the Swarm cards." /></div>
        ) : (
          <>
            <div className="mb-5 grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
              {table.agents.map((a) => (
                <div key={a.agent} className="card p-4" style={{ boxShadow: `inset 0 3px 0 0 ${agentColor(a.agent)}` }}>
                  <div className="flex items-center justify-between gap-2">
                    <AgentChip agent={a.agent} />
                    <StatusBadge status={a.status} />
                  </div>
                  <div className="mt-3 grid grid-cols-3 gap-2 text-center">
                    <Stat label="pass" value={fmtPct(a.pass_rate)} tone={a.pass_rate === null ? undefined : a.pass_rate >= 0.7 ? "success" : a.pass_rate >= 0.4 ? "warn" : "danger"} />
                    <Stat label="per run" value={fmtSecs(a.avg_wall_s)} />
                    <Stat label="per step" value={fmtSecs(a.avg_step_s)} tone={a.slow_step_rate && a.slow_step_rate >= 0.3 ? "warn" : undefined} />
                  </div>
                  <div className="mt-2 text-[11px] text-dim">{a.n} attempt{a.n === 1 ? "" : "s"} · {a.steps} steps{a.slow_steps ? ` · ${a.slow_steps} slow` : ""}</div>
                  {a.probation.length ? (
                    <div className="mt-2 rounded-md border border-warn/30 bg-warn/8 px-2 py-1.5 text-[11.5px] text-warn">
                      probation: {a.probation.map((p) => `${p.task_type} (${p.status})`).join(", ")}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
            <div className="card overflow-x-auto">
              <table className="w-full min-w-[860px] border-collapse text-[12.5px]">
                <thead className="border-b border-border bg-surface-2/60">
                  <tr>
                    {th("agent", "agent")}{th("work", "type")}{th("status", "status")}{th("n", undefined, true)}{th("pass", undefined, true)}{th("per attempt", "wall", true)}{th("per step", "step", true)}{th("p90 step", undefined, true)}{th("slow", undefined, true)}{th("trend", undefined, true)}{th("why")}
                  </tr>
                </thead>
                <tbody>
                  {cells.map((c) => (
                    <tr key={`${c.agent}-${c.task_type}`} className="border-b border-border/60 last:border-0 hover:bg-surface-2/50">
                      <td className="px-2 py-1.5"><AgentChip agent={c.agent} size="sm" /></td>
                      <td className="px-2 py-1.5 text-muted">{c.task_type}</td>
                      <td className="px-2 py-1.5"><StatusBadge status={c.status} /></td>
                      <td className="num px-2 py-1.5 text-right">{c.n}</td>
                      <td className={cn("num px-2 py-1.5 text-right", c.pass_rate !== null && (c.pass_rate >= 0.7 ? "text-success" : c.pass_rate >= 0.4 ? "text-warn" : "text-danger"))}>{fmtPct(c.pass_rate)}</td>
                      <td className="num px-2 py-1.5 text-right">{fmtSecs(c.avg_wall_s)}</td>
                      <td className="num px-2 py-1.5 text-right">{fmtSecs(c.avg_step_s)}</td>
                      <td className="num px-2 py-1.5 text-right text-muted">{fmtSecs(c.p90_step_s)}</td>
                      <td className={cn("num px-2 py-1.5 text-right", (c.slow_step_rate || 0) >= 0.3 && "text-warn")}>{c.steps ? `${c.slow_steps}/${c.steps}` : "–"}</td>
                      <td className={cn("num px-2 py-1.5 text-right", c.trend !== null && c.trend >= 1.5 && "text-warn", c.trend !== null && c.trend <= 0.7 && "text-success")}>{c.trend === null ? "–" : `${c.trend.toFixed(1)}×`}</td>
                      <td className="px-2 py-1.5 text-[11.5px] text-dim">{c.why}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-2 text-[11px] text-dim">per attempt = wall time from start to verified · per step = one tool call · slow = steps over {table.slow_step_s}s · trend = last few attempts vs the ones before (above 1 is getting slower)</div>
          </>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "success" | "warn" | "danger" }) {
  return (
    <div className="rounded-md bg-surface-2 px-2 py-1.5">
      <div className={cn("num text-[16px] font-semibold", tone === "success" && "text-success", tone === "warn" && "text-warn", tone === "danger" && "text-danger")}>{value}</div>
      <div className="text-[10.5px] uppercase tracking-wider text-dim">{label}</div>
    </div>
  );
}

export function AgentsPage() {
  const agents = useStore((s) => s.agents);
  const tasks = useStore((s) => s.tasks);
  const now = Date.now() / 1000;
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-6 py-5">
        <h1 className="mb-4 flex items-center gap-2 text-[18px] font-semibold tracking-tight"><Bot className="h-4.5 w-4.5 text-accent" /> Agents</h1>
        <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))" }}>
          {agents.map((a) => {
            const working = tasks.filter((t) => isLive(t) && taskAgent(t) === a.agent_id);
            return (
              <div key={a.agent_id + a.host} className="card p-4" style={{ boxShadow: `inset 0 3px 0 0 ${agentColor(a.agent_id)}` }}>
                <div className="flex items-center gap-2">
                  <AgentChip agent={a.agent_id} />
                  <span className={cn("ml-auto flex items-center gap-1.5 text-[12px]", a.alive ? "text-success" : "text-danger")}><span className={cn("h-1.5 w-1.5 rounded-full", a.alive ? "bg-success live-dot" : "bg-danger")} />{a.alive ? "alive" : "gone"}</span>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-1 text-[12px] text-muted">
                  <span>host</span><span className="mono text-fg">{a.host || "—"}</span>
                  <span>lanes</span><span className="num text-fg">{a.busy || 0} / {a.capacity || 1} busy</span>
                  <span>last seen</span><span className="text-fg">{age(a.last_seen, now)} ago</span>
                  <span>can do</span><span className="text-fg">{(a.capabilities || []).join(", ")}</span>
                </div>
                {working.length ? (
                  <div className="mt-3 grid gap-1">
                    {working.map((t) => (
                      <Link key={t.id} to={taskHref(t)} className="flex items-center gap-2 rounded-md bg-surface-2 px-2 py-1 text-[12px] hover:bg-surface-3">
                        <TaskKindIcon t={t} /><span className="mono text-muted">{short(t.id)}</span><span className="truncate">{firstLine(t.spec, 60)}</span>
                        {sessionOf(t).attention ? <span className="ml-auto text-warn">needs you</span> : null}
                      </Link>
                    ))}
                  </div>
                ) : <div className="mt-3 text-[12px] text-dim">idle</div>}
              </div>
            );
          })}
          {!agents.length ? <div className="card col-span-full"><EmptyState title="No agents registered" hint="Start hiveswarm-worker (hm up does it for you); hub-local lanes (a local model on the hub) are checked directly." /></div> : null}
        </div>
      </div>
    </div>
  );
}
