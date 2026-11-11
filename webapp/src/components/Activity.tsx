import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { AgentStatus, AgentTable, RulesetRow, Task } from "@/lib/types";
import { HOME, age, agentColor, agentLabel, classifyEntry, cn, firstLine, fmtPct, fmtSecs, isLive, roleOf, short, taskAgent } from "@/lib/utils";
import { AgentChip, EmptyState, StatusBadge, TaskKindIcon } from "./bits";
import { StateBadge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { Input } from "./ui/fields";
import { EventStream } from "./EventStream";

const StatsCharts = lazy(() => import("@/pages/StatsCharts"));

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

/** Every task and agent the hub remembers, newest first; a row opens its record. */
export function HistoryTable() {
  const all = useStore((s) => s.tasks);
  const openDetail = useStore((s) => s.openDetail);
  const [q, setQ] = useState("");
  const [more, setMore] = useState(false);
  const tasks = useMemo(() => all.filter((t) => t.project !== HOME), [all]);
  const found = useMemo(() => tasks.filter((t) => matches(t, q)), [tasks, q]);
  const rows = more || q.trim() ? found : found.slice(0, 12);
  const now = Date.now() / 1000;
  return (
    <div data-history>
      <div className="mb-2 flex items-center gap-3">
        <div className="text-label text-fg">History <span className="num font-normal text-dim">{found.length}</span></div>
        <span className="flex-1" />
        <div className="relative w-full max-w-sm">
          <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-dim" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="state:failed  agent:codex  project:demo  or free text" className="pl-8" />
        </div>
      </div>
      {rows.length ? (
        <Card className="overflow-hidden">
          <table className="w-full border-collapse text-[13px]">
            <thead>
              <tr className="border-b border-border">
                <th className={TH}>id</th>
                <th className={TH}>state</th>
                <th className={TH}>agent</th>
                <th className={TH}>project</th>
                <th className={cn(TH, "text-right")}>age</th>
                <th className={TH}>what</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.id} className="h-9 cursor-default border-b border-border last:border-0 hover:bg-surface-2" onClick={() => openDetail(t.id)} data-history-row={t.id}>
                  <td className="mono px-3 py-1 text-muted">{short(t.id, 8)}</td>
                  <td className="px-3 py-1"><StateBadge state={t.state} live={isLive(t)} /></td>
                  <td className="px-3 py-1"><span className="inline-flex items-center gap-1.5"><TaskKindIcon t={t} /><span className="h-1.5 w-1.5 rounded-full" style={{ background: agentColor(taskAgent(t)) }} />{taskAgent(t) ? agentLabel(taskAgent(t)) : <span className="text-dim">—</span>}</span></td>
                  <td className="px-3 py-1 text-muted">{t.project}</td>
                  <td className="num px-3 py-1 text-right text-dim">{age(t.updated_at, now)}</td>
                  <td className="max-w-[460px] truncate px-3 py-1">{roleOf(t) === "chat" ? "chat agent" : roleOf(t) === "lead" ? "lead" : firstLine(t.spec, 120)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : (
        <Card><EmptyState className="min-h-[120px]" title={q ? "Nothing matches" : "Nothing yet"} hint={q ? "Try a broader filter." : "Tasks and agents appear here once a lead starts work."} /></Card>
      )}
      {!more && !q.trim() && found.length > rows.length ? <div className="mt-2"><Button size="xs" variant="ghost" onClick={() => setMore(true)}>show all {found.length}</Button></div> : null}
    </div>
  );
}

/** How the agents coordinate: routing, handoffs, approvals, answers, verification, overlaps. */
export function FeedPanel() {
  const feed = useStore((s) => s.feed);
  const homeIds = useStore((s) => s.tasks).filter((t) => t.project === HOME).map((t) => t.id).join(",");
  const [q, setQ] = useState("");
  const [only, setOnly] = useState<"all" | "coord" | "you">("coord");
  const entries = useMemo(() => {
    const home = new Set(homeIds.split(","));
    let rows = feed.filter((e) => !home.has(e.task_id));
    if (only === "coord") rows = rows.filter((e) => { const { kind } = classifyEntry(e.source, e.chunk); return !["out", "think", "result", "tool", "msg", "info"].includes(kind); });
    if (only === "you") rows = rows.filter((e) => { const { kind } = classifyEntry(e.source, e.chunk); return ["you", "ask", "warn"].includes(kind); });
    if (q.trim()) {
      const s = q.trim().toLowerCase();
      rows = rows.filter((e) => e.task_id.includes(s) || e.source.toLowerCase().includes(s) || e.chunk.toLowerCase().includes(s));
    }
    return rows;
  }, [feed, q, only, homeIds]);
  return (
    <div data-feed>
      <div className="mb-2 flex items-center gap-3">
        <div className="text-label text-fg">Feed</div>
        <span className="flex-1" />
        <div className="flex items-center rounded-md bg-surface-2 p-0.5">
          {(["coord", "you", "all"] as const).map((k) => (
            <Button key={k} size="xs" variant="ghost" className={cn(only === k && "bg-surface text-fg shadow-sm")} onClick={() => setOnly(k)}>{k === "coord" ? "coordination" : k === "you" ? "asks and answers" : "everything"}</Button>
          ))}
        </div>
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="filter" className="h-7 w-44" />
      </div>
      <Card className="h-[300px] overflow-hidden">
        <EventStream entries={entries} showTask showAgent emptyText="The hive is quiet." />
      </Card>
    </div>
  );
}

/** How each agent does on each kind of work: pass rate, wall time, step times, and who is on probation. */
export function StatsPanel() {
  const [table, setTable] = useState<AgentTable | null>(null);
  const [rulesets, setRulesets] = useState<RulesetRow[]>([]);
  const [err, setErr] = useState("");
  const [sort, setSort] = useState<"status" | "agent" | "type" | "wall" | "step">("status");
  const [details, setDetails] = useState(false);
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
    <div data-stats>
      <div className="mb-2 flex items-end justify-between gap-3">
        <div>
        <div className="text-label text-fg">Agents at work</div>
        <div className="text-[12px] text-muted">Pass rate, time per run and per tool step. A <b className="font-medium text-fg">slow</b> or <b className="font-medium text-fg">failing</b> agent is on probation for that kind of work: it keeps getting easy tasks of that type until it recovers. A step over {table?.slow_step_s ?? 120}s counts as slow.</div>
        </div>
        {table?.cells.length ? <Button size="sm" variant="secondary" onClick={() => setDetails((v) => !v)} data-stats-details>{details ? "hide details" : "charts and table"}</Button> : null}
      </div>
        {err ? <Card className="p-4 text-[12px] text-danger">{err}</Card> : !table ? <div className="text-[12px] text-dim">loading</div> : !table.cells.length ? (
        <Card><EmptyState title="No finished attempts yet" className="min-h-[120px]" hint="Numbers appear after the first verified attempts." /></Card>
      ) : (
        <>
          <div className={cn("grid gap-4", details && "mb-6")} style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
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
          {details ? (
          <>
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
          ) : null}
        </>
      )}
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
