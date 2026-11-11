import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { Agent, LocalAgent, LocalAgentsInfo } from "@/lib/types";
import { HOME, age, agentLabel, cn, firstLine, isLive, loudAttention, roleOf, short, taskAgent } from "@/lib/utils";
import { AgentChip, EmptyState, TaskKindIcon } from "./bits";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { LocalAgents } from "./LocalAgents";

const MAX_LANES = 32;
const CROWDED_LANES = 6;

/** Lanes an agent is asked to run: an override from `hm agents --set` when one is set, else its worker.toml. */
function wantedLanes(a: Agent): number {
  return a.desired_capacity ?? a.configured ?? a.capacity ?? 1;
}

/** The +/− on an agent's card. One number per agent: `concurrency` in the worker's worker.toml. When that file is on
 *  this machine the control edits it (and the table below shows the same number); for a worker elsewhere it sets the
 *  daemon-side override that `hm agents --set` uses, and says so. Hub lanes have no control. */
function LaneControl({ a, providerLanes, profile, reloadProfiles }: { a: Agent; providerLanes: number; profile: LocalAgent | null; reloadProfiles: () => Promise<void> }) {
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
        const r = await api.setLocalAgent({ name: a.agent_id, concurrency: n });
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
  const owner = profile?.profile || a.profile;
  if (owner === "_chat") {
    return (
      <div className="mt-3 border-t border-border pt-3">
        <div className="flex items-center gap-2">
          <span className="text-label">Lanes</span>
          <span className="num text-meta">{a.busy || 0} of {a.capacity ?? 1} busy</span>
          <span className="num ml-auto text-[13px] font-semibold text-fg">{a.capacity ?? 1}</span>
        </div>
        <div className="mt-1 text-meta">one lane per chat agent that is open, plus a spare; the hub keeps the count</div>
      </div>
    );
  }
  if (owner) {
    return (
      <div className="mt-3 border-t border-border pt-3">
        <div className="flex items-center gap-2">
          <span className="text-label">Lanes</span>
          <span className="num text-meta">{a.busy || 0} of {a.capacity ?? 1} busy</span>
          <span className="num ml-auto text-[13px] font-semibold text-fg">{a.configured ?? a.capacity}</span>
        </div>
        <div className="mt-1 text-meta">from profile {owner}; the count is set per project, in the project's settings</div>
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

/** Every agent a worker has registered and the lanes the hub runs itself, then this machine's worker.toml. */
export function AgentsSection() {
  const agents = useStore((s) => s.agents);
  const tasks = useStore((s) => s.tasks);
  const openDetail = useStore((s) => s.openDetail);
  const now = Date.now() / 1000;
  const [local, setLocal] = useState<LocalAgentsInfo | null>(null);
  const reloadLocal = useCallback(() => api.localAgents().then(setLocal).catch(() => setLocal({ ok: false, reason: "not reachable", agents: [], adapters: [] })), []);
  useEffect(() => { reloadLocal(); }, [reloadLocal]);
  const tableOf = (a: Agent): LocalAgent | null =>
    (local?.ok && a.host === local.host && local.agents.find((p) => p.name === a.agent_id)) || null;
  const lanesByProvider = useMemo(() => {
    const m: Record<string, number> = {};
    for (const a of agents) {
      const p = a.provider || a.agent_id;
      m[p] = (m[p] || 0) + wantedLanes(a);
    }
    return m;
  }, [agents]);
  return (
    <div data-agents>
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
                {a.profile && a.profile !== "_chat" ? <><span>profile</span><span className="text-fg">{a.profile}</span></> : null}
                {a.local ? null : <><span>last seen</span><span className="text-fg">{age(a.last_seen, now)} ago</span></>}
                <span>can do</span><span className="text-fg">{a.profile === "_chat" ? "chat (reads the swarm, steers the lead; no code)" : (a.capabilities || []).join(", ") || "—"}</span>
              </div>
              <LaneControl a={a} providerLanes={lanesByProvider[a.provider || a.agent_id] || 0} profile={tableOf(a)} reloadProfiles={reloadLocal} />
              {working.length ? (
                <div className="mt-3 grid gap-1">
                  {working.map((t) => (
                    <button type="button" key={t.id} onClick={() => openDetail(t.id)} className="flex min-w-0 items-center gap-2 rounded-md bg-surface-2 px-2 py-1 text-left text-[12px] hover:bg-surface-3">
                      <TaskKindIcon t={t} /><span className="mono shrink-0 text-muted">{short(t.id)}</span><span className="min-w-0 flex-1 truncate">{roleOf(t) === "chat" ? `chat agent · ${t.project === HOME ? "Home" : t.project}` : roleOf(t) === "lead" ? `lead · ${t.project}` : firstLine(t.spec, 60)}</span>
                      {loudAttention(t) ? <span className="shrink-0 whitespace-nowrap text-accent">needs you</span> : null}
                    </button>
                  ))}
                </div>
              ) : <div className="mt-3 text-[12px] text-dim">idle</div>}
            </Card>
          );
        })}
        {!agents.length ? <Card className="col-span-full"><EmptyState title="No agents registered" hint="Start hiveswarm-worker (hm up does it for you); hub-local lanes (a local model on the hub) are checked directly." /></Card> : null}
      </div>
      <LocalAgents info={local} reload={reloadLocal} />
    </div>
  );
}
