import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { AdapterInfo, Agent, LocalAgentsInfo, ProfileCoder, ProfileRole, ProjectProfile, SwarmProfile } from "@/lib/types";
import { agentLabel, cn } from "@/lib/utils";
import { AgentChip, EmptyState } from "./bits";
import { DEFAULT, ModelPicker, effortOptions } from "./LocalAgents";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from "./ui/dialog";
import { Input, Label, Select, Tip } from "./ui/fields";

const MAX_COUNT = 32;
const HUB_ADAPTERS = ["prime_agent", "local_direct"];

/** What a role runs, after the adapter's name: `opus · effort max`. */
export function roleText(r: ProfileRole | null | undefined): string {
  if (!r) return "";
  return [r.model || "default model", r.effort ? `effort ${r.effort}` : null].filter(Boolean).join(" · ");
}

/** One line for a whole profile: `Claude Code opus max leads · sonnet chats · Claude Code opus medium ×2, Codex ×1`. */
export function profileLine(p: { lead: ProfileRole | null; chat: ProfileRole | null; coders: ProfileCoder[] }): string {
  const role = (r: ProfileRole | null) => (r ? [agentLabel(r.adapter), r.model, r.effort].filter(Boolean).join(" ") : "?");
  const coders = p.coders.map((c) => `${role(c)} ×${c.count}`).join(", ");
  return `${role(p.lead)} leads · ${p.chat?.model || "default"} chats · ${coders}`;
}

function RoleState({ live, hub, chat, idle }: { live?: Agent | null; hub?: boolean; chat?: boolean; idle?: string }) {
  if (chat) return <span className="whitespace-nowrap text-meta">the advisor</span>;
  if (!live) return <span className="whitespace-nowrap text-meta">{hub ? "hub lane not reachable" : idle || "starts with a project"}</span>;
  const word = hub ? (live.alive ? "up" : "down") : live.alive ? "alive" : "gone";
  return (
    <span className="flex items-center gap-2 whitespace-nowrap">
      <span className={cn("flex items-center gap-1.5 text-[12px]", live.alive ? "text-muted" : "text-danger")}><span className={cn("h-1.5 w-1.5 rounded-full", live.alive ? "bg-success" : "bg-danger")} />{word}</span>
      {live.alive ? <span className="num text-meta">{live.busy || 0} of {live.capacity ?? 1} busy</span> : null}
    </span>
  );
}

function Stepper({ value, onChange, min = 0, max = MAX_COUNT, label }: { value: number; onChange: (n: number) => void; min?: number; max?: number; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <Button size="icon-xs" variant="secondary" aria-label={`fewer ${label}`} disabled={value <= min} onClick={() => onChange(value - 1)}>−</Button>
      <span className="num w-6 text-center text-[13px] font-semibold text-fg">{value}</span>
      <Button size="icon-xs" variant="secondary" aria-label={`more ${label}`} disabled={value >= max} onClick={() => onChange(value + 1)}>+</Button>
    </span>
  );
}

export interface ProfileView {
  name: string;
  note?: string;
  error?: string | null;
  lead: ProfileRole | null;
  chat: ProfileRole | null;
  coders: ProfileCoder[];
}

/** A profile as one card: the lead and chat rows, a hairline, then a row per coder. `onCount` adds a ×n stepper
 *  to each coder (the per-project lane count); `right` goes into the header's action slot. */
export function ProfileCard({ p, agents, right, onCount, className, compact, idle }: { p: ProfileView; agents: Agent[]; right?: React.ReactNode; onCount?: (c: ProfileCoder, n: number) => void; className?: string; compact?: boolean; idle?: string }) {
  const live = (id?: string) => (id ? agents.find((a) => a.agent_id === id) : undefined);
  const row = (role: string, r: ProfileRole | null, opts: { lead?: boolean; chat?: boolean; coder?: ProfileCoder } = {}) => (
    <div key={role + (opts.coder?.key || "")} className="flex h-9 items-center gap-3 px-4 text-[12.5px]" data-role={role}>
      <span className="w-10 shrink-0 text-label">{role}</span>
      <span className="shrink-0">{r ? <AgentChip agent={r.agent_id || r.adapter} provider={r.adapter} label={agentLabel(r.adapter)} lead={opts.lead} size="sm" /> : <span className="text-meta">—</span>}</span>
      <span className="min-w-0 flex-1 truncate text-muted">{r?.model ? <span className="mono text-fg">{r.model}</span> : <span>default model</span>}{r?.effort ? <> · effort {r.effort}</> : null}</span>
      {opts.coder ? (
        <span className="num shrink-0 text-[12px] text-fg">
          {onCount && !opts.coder.hub ? <Stepper value={opts.coder.count} onChange={(n) => onCount(opts.coder!, n)} label={`${agentLabel(opts.coder.adapter)} lanes`} /> : <>×{opts.coder.count}</>}
        </span>
      ) : null}
      <span className="shrink-0"><RoleState live={live(r?.agent_id)} hub={opts.coder?.hub} chat={opts.chat} idle={idle} /></span>
    </div>
  );
  return (
    <Card className={cn("overflow-hidden", className)} data-profile-card={p.name}>
      {!compact ? (
        <div className="flex min-h-9 items-center gap-2 border-b border-border px-4">
          <span className="shrink-0 text-[13px] font-medium text-fg">{p.name}</span>
          {p.note ? <span className="min-w-0 truncate text-[12px] text-muted">{p.note}</span> : null}
          {right ? <div className="ml-auto flex shrink-0 items-center gap-1 whitespace-nowrap">{right}</div> : null}
        </div>
      ) : null}
      {p.error ? <div className="px-4 py-3 text-[12px] text-danger">{p.error}</div> : (
        <>
          {row("lead", p.lead, { lead: true })}
          {row("chat", p.chat, { chat: true })}
          <div className="border-t border-border" />
          {p.coders.map((c) => row("coder", c, { coder: c }))}
        </>
      )}
    </Card>
  );
}

// ── editor ──────────────────────────────────────────────────────────────

type RoleDraft = { adapter: string; model: string; effort: string };
type CoderDraft = RoleDraft & { count: number; id: number };
let nextId = 1;

function RoleFields({ r, onChange, adapters, adapterOptions, disabled, chat }: { r: RoleDraft; onChange: (r: RoleDraft) => void; adapters: AdapterInfo[]; adapterOptions: { value: string; label: string }[]; disabled?: boolean; chat?: boolean }) {
  const info = adapters.find((a) => a.name === r.adapter);
  const hub = HUB_ADAPTERS.includes(r.adapter);
  return (
    <>
      {chat ? <span className="flex h-8 items-center text-[13px] text-muted">Claude Code</span> : (
        <Select value={r.adapter} onChange={(v) => onChange({ adapter: v, model: "", effort: "" })} options={adapterOptions} className={cn("h-8", disabled && "pointer-events-none opacity-60")} />
      )}
      {hub ? <span className="flex h-8 items-center text-[12px] text-muted">the hub's own model</span> : (
        <span className={cn(disabled && "pointer-events-none opacity-60")}><ModelPicker adapter={info} value={r.model} onChange={(v) => onChange({ ...r, model: v })} className="h-8 w-full" /></span>
      )}
      {info?.effort_supported && !hub ? (
        <Select value={r.effort || DEFAULT} placeholder="default" onChange={(v) => onChange({ ...r, effort: v === DEFAULT ? "" : v })} options={effortOptions(info)} className={cn("h-8", disabled && "pointer-events-none opacity-60")} />
      ) : <span className="flex h-8 items-center text-meta">{hub ? "" : "no effort setting"}</span>}
    </>
  );
}

function fromProfile(p: SwarmProfile | null): { name: string; note: string; lead: RoleDraft; chat: RoleDraft; coders: CoderDraft[] } {
  const role = (r: ProfileRole | null | undefined, adapter = "claude_code"): RoleDraft => ({ adapter: r?.adapter || adapter, model: r?.model || "", effort: r?.effort || "" });
  return {
    name: p?.name || "",
    note: p?.note || "",
    lead: role(p?.lead),
    chat: role(p?.chat),
    coders: p?.coders.length ? p.coders.map((c) => ({ ...role(c), count: c.count, id: nextId++ })) : [{ adapter: "claude_code", model: "", effort: "", count: 1, id: nextId++ }],
  };
}

/** Create or change a profile. One a started project runs on can only change its note; "save as a copy" makes a new one. */
export function ProfileEditor({ open, onOpenChange, initial, onSaved }: { open: boolean; onOpenChange: (o: boolean) => void; initial: SwarmProfile | null; onSaved: (p: SwarmProfile) => void }) {
  const agents = useStore((s) => s.agents);
  const [info, setInfo] = useState<LocalAgentsInfo | null>(null);
  const [d, setD] = useState(() => fromProfile(initial));
  const [copy, setCopy] = useState(false);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!open) return;
    setD(fromProfile(initial));
    setCopy(false);
    api.localAgents().then(setInfo).catch(() => setInfo({ ok: false, agents: [], adapters: [] }));
  }, [open, initial]);
  const adapters = info?.adapters || [];
  const cli = adapters.filter((a) => a.installed);
  const hubLanes = agents.filter((a) => a.local);
  const leadOptions = useMemo(() => (cli.length ? cli : adapters).map((a) => ({ value: a.name, label: agentLabel(a.name) })), [cli, adapters]);
  const coderOptions = useMemo(() => [...leadOptions, ...hubLanes.map((a) => ({ value: a.agent_id, label: `${agentLabel(a.agent_id)} (hub lane)` }))], [leadOptions, hubLanes]);
  const locked = !!initial?.locked && !copy;
  const nameOk = /^[A-Za-z0-9_-]{1,24}$/.test(d.name);
  const save = async () => {
    setSaving(true);
    try {
      const r = await api.setProfile({
        name: d.name.trim(), note: d.note.trim(),
        lead: { adapter: d.lead.adapter, model: d.lead.model || null, effort: d.lead.effort || null },
        chat: { model: d.chat.model || null, effort: d.chat.effort || null },
        coders: d.coders.map((c) => ({ adapter: c.adapter, model: c.model || null, effort: c.effort || null, count: c.count })),
      });
      toast(initial && !copy ? `Profile ${r.name} saved` : `Profile ${r.name} created`);
      onSaved(r);
      onOpenChange(false);
    } catch (e) {
      toast.error(String((e as Error).message));
    } finally {
      setSaving(false);
    }
  };
  const setCoder = (id: number, patch: Partial<CoderDraft>) => setD({ ...d, coders: d.coders.map((c) => (c.id === id ? { ...c, ...patch } : c)) });
  const grid = "grid grid-cols-[48px_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_92px_64px] items-center gap-2";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent wide data-profile-editor>
        <DialogHeader title={initial && !copy ? `Profile ${initial.name}` : copy ? `Copy of ${initial?.name}` : "New profile"}
          description="Which models lead the goal, chat as the advisor, and code the tasks, with lanes per coder. A project runs on one profile from its first task on." />
        <DialogBody className="max-h-[70vh] overflow-y-auto">
          {locked ? (
            <div className="mb-4 flex items-center gap-3 rounded-md bg-surface-2 px-3 py-2 text-[12px]" data-profile-locked>
              <span className="text-fg">In use by {initial!.used_by.filter((u) => u.started).map((u) => u.project).join(", ")}, which started: only the note can change.</span>
              <span className="flex-1" />
              <Button size="xs" variant="secondary" onClick={() => { setCopy(true); setD({ ...d, name: `${d.name}-2`.slice(0, 24) }); }}>save as a copy</Button>
            </div>
          ) : null}
          <div className="grid gap-4">
            <div className="grid grid-cols-[260px_1fr] gap-3">
              <div>
                <Label hint="letters, digits, - and _">Name</Label>
                <Input value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder="deep" disabled={!!initial && !copy} autoFocus={!initial} data-profile-name />
              </div>
              <div>
                <Label>Note</Label>
                <Input value={d.note} onChange={(e) => setD({ ...d, note: e.target.value })} placeholder="Opus leads, two Opus medium lanes, Codex for tests" />
              </div>
            </div>
            <div className={grid + " text-meta"}>
              <span /><span>agent</span><span>model</span><span>effort</span><span /><span />
            </div>
            <div className={grid} data-role-fields="lead">
              <Label className="mb-0">Lead</Label>
              <RoleFields r={d.lead} onChange={(r) => setD({ ...d, lead: r })} adapters={adapters} adapterOptions={leadOptions} disabled={locked} />
              <span /><span />
            </div>
            <div className={grid} data-role-fields="chat">
              <Label className="mb-0">Chat</Label>
              <RoleFields r={{ ...d.chat, adapter: "claude_code" }} onChange={(r) => setD({ ...d, chat: r })} adapters={adapters} adapterOptions={leadOptions} disabled={locked} chat />
              <span /><span />
            </div>
            {d.coders.map((c, i) => (
              <div key={c.id} className={grid} data-role-fields="coder">
                <Label className="mb-0">{i === 0 ? "Coders" : ""}</Label>
                <RoleFields r={c} onChange={(r) => setCoder(c.id, { ...r, count: HUB_ADAPTERS.includes(r.adapter) ? 1 : c.count })} adapters={adapters} adapterOptions={coderOptions} disabled={locked} />
                <span className={cn(locked && "pointer-events-none opacity-60")}>{HUB_ADAPTERS.includes(c.adapter) ? <span className="num text-[13px] text-muted">×1</span> : <Stepper value={c.count} onChange={(n) => setCoder(c.id, { count: n })} min={1} label="lanes" />}</span>
                <Button size="xs" variant="ghost" className="hover:text-danger" disabled={locked || d.coders.length === 1} onClick={() => setD({ ...d, coders: d.coders.filter((x) => x.id !== c.id) })}>remove</Button>
              </div>
            ))}
            <div>
              <Button size="sm" variant="secondary" disabled={locked} onClick={() => setD({ ...d, coders: [...d.coders, { adapter: d.coders[d.coders.length - 1]?.adapter || "claude_code", model: "", effort: "", count: 1, id: nextId++ }] })}>add coder</Button>
              <span className="ml-3 text-meta">Two coders that run the same thing merge into one with the lanes added.</span>
            </div>
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={!nameOk || saving} onClick={save}>{saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}{copy ? "Create copy" : initial ? "Save" : "Create profile"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── the Agents page section ─────────────────────────────────────────────

export function ProfilesSection({ reloadLocal }: { reloadLocal: () => Promise<void> }) {
  const agents = useStore((s) => s.agents);
  const [list, setList] = useState<SwarmProfile[] | null>(null);
  const [editing, setEditing] = useState<SwarmProfile | null>(null);
  const [open, setOpen] = useState(false);
  const reload = useCallback(() => api.profiles().then((r) => setList(r.profiles)).catch(() => setList([])), []);
  useEffect(() => { reload(); }, [reload]);
  const remove = async (p: SwarmProfile) => {
    try {
      await api.deleteProfile(p.name);
      toast(`Profile ${p.name} removed`);
      await reload();
    } catch (e) {
      toast.error(String((e as Error).message));
    }
  };
  return (
    <section className="mb-8" data-profiles>
      <div className="mb-2 flex items-end justify-between gap-3">
        <div>
          <div className="text-label text-fg">Profiles</div>
          <div className="text-[12px] text-muted">A profile fixes which models lead, chat and code for a project, with lanes per coder. Pick one when you start a goal; from the project's first task on it stays, only its lane counts change.</div>
        </div>
        <Button size="sm" variant="secondary" onClick={() => { setEditing(null); setOpen(true); }} data-new-profile>new profile</Button>
      </div>
      {list === null ? null : list.length ? (
        <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(540px, 1fr))" }}>
          {list.map((p) => (
            <ProfileCard key={p.name} p={p} agents={agents} right={
              <>
                {p.used_by.length ? <span className="mr-1 text-meta">on {p.used_by.map((u) => u.project).join(", ")}</span> : null}
                <Button size="xs" variant="ghost" onClick={() => { setEditing(p); setOpen(true); }}>edit</Button>
                {p.used_by.length ? <Tip label={`In use by ${p.used_by.map((u) => u.project).join(", ")}`}><span><Button size="xs" variant="ghost" disabled>remove</Button></span></Tip>
                  : <Button size="xs" variant="ghost" className="hover:text-danger" onClick={() => remove(p)}>remove</Button>}
              </>
            } />
          ))}
        </div>
      ) : (
        <Card><EmptyState className="min-h-[120px]" title="No profiles yet" hint="A profile names the models for a project's lead, advisor and coders. Make one here, then pick it in the New goal dialog." /></Card>
      )}
      <ProfileEditor open={open} onOpenChange={setOpen} initial={editing} onSaved={async () => { await reload(); await api.materialize().catch(() => undefined); await reloadLocal(); }} />
    </section>
  );
}

// ── the New goal dialog's picker ────────────────────────────────────────

export const NO_PROFILE = "__none__";
const NEW_PROFILE = "__new__";

/** Which profile a project runs on. For an existing project the choice is saved at once and locked once the project
 *  has a task (its coders' lane counts stay editable here); for a project about to be created the choice is kept by
 *  the dialog and applied after creation. */
export function ProfilePicker({ project, value, onChange }: { project: string | null; value: string; onChange: (v: string) => void }) {
  const agents = useStore((s) => s.agents);
  const [list, setList] = useState<SwarmProfile[]>([]);
  const [view, setView] = useState<ProjectProfile | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const reloadList = useCallback(() => api.profiles().then((r) => setList(r.profiles)).catch(() => setList([])), []);
  const reloadView = useCallback(() => {
    if (!project) { setView(null); return Promise.resolve(); }
    return api.projectProfile(project).then((v) => { setView(v); onChange(v.profile || NO_PROFILE); }).catch(() => setView(null));
  }, [project, onChange]);
  useEffect(() => { reloadList(); }, [reloadList]);
  useEffect(() => { reloadView(); }, [reloadView]);
  const chosen = value === NO_PROFILE ? null : list.find((p) => p.name === value) || null;
  const locked = !!view?.locked;
  const select = async (v: string) => {
    if (v === NEW_PROFILE) { setEditorOpen(true); return; }
    onChange(v);
    if (!project) return;
    try {
      await api.setProjectSettings(project, { profile: v === NO_PROFILE ? "" : v });
      api.materialize().catch(() => undefined);
      await reloadView();
    } catch (e) {
      toast.error(String((e as Error).message));
      await reloadView();
    }
  };
  const setCount = async (c: ProfileCoder, n: number) => {
    if (!project || !view?.resolved) return;
    const lanes: Record<string, number> = {};
    for (const x of view.resolved.coders) if (!x.hub) lanes[x.agent_id] = x.agent_id === c.agent_id ? n : x.count;
    try {
      await api.setProjectSettings(project, { lanes });
      api.materialize().catch(() => undefined);
      await reloadView();
    } catch (e) {
      toast.error(String((e as Error).message));
    }
  };
  const options = [{ value: NO_PROFILE, label: "Any agent", hint: "whatever is alive, or the roster below" }, ...list.map((p) => ({ value: p.name, label: p.name, hint: p.note || profileLine(p) })), { value: NEW_PROFILE, label: "New profile…" }];
  const shown: ProfileView | null = view?.resolved && view.profile === value ? view.resolved : chosen;
  return (
    <div data-profile-picker data-locked={locked ? "true" : "false"}>
      <Label hint={locked ? (view!.profile ? `fixed since ${project} started (${view!.task_count} task${view!.task_count === 1 ? "" : "s"}); lanes still change` : `fixed since ${project} started: it runs on any agent`) : "the models for the lead, the advisor and the coders"}>Profile</Label>
      <div className={cn(locked && "pointer-events-none opacity-60")}>
        <Select value={value} onChange={select} options={options} />
      </div>
      {shown ? (
        <div className="mt-2">
          <ProfileCard p={shown} agents={agents} compact onCount={project && view?.resolved ? setCount : undefined} idle={project && view?.profile === value ? "lanes start within a minute" : undefined} />
        </div>
      ) : null}
      <ProfileEditor open={editorOpen} onOpenChange={setEditorOpen} initial={null} onSaved={async (p) => { await reloadList(); await select(p.name); }} />
    </div>
  );
}

// ── the Lead page's strip ───────────────────────────────────────────────

export function ProfileStrip({ project }: { project: string }) {
  const agents = useStore((s) => s.agents);
  const name = useStore((s) => s.projects[project]?.profile || null);
  const [view, setView] = useState<ProjectProfile | null>(null);
  const [open, setOpen] = useState(false);
  const reload = useCallback(() => api.projectProfile(project).then(setView).catch(() => setView(null)), [project]);
  useEffect(() => { if (name) reload(); else setView(null); }, [name, reload]);
  if (!name || !view?.resolved) return null;
  const r = view.resolved;
  const live = (id?: string) => (id ? agents.find((a) => a.agent_id === id) : undefined);
  const setCount = async (c: ProfileCoder, n: number) => {
    const lanes: Record<string, number> = {};
    for (const x of r.coders) if (!x.hub) lanes[x.agent_id] = x.agent_id === c.agent_id ? n : x.count;
    try {
      await api.setProjectSettings(project, { lanes });
      api.materialize().catch(() => undefined);
      await reload();
    } catch (e) {
      toast.error(String((e as Error).message));
    }
  };
  const dot = (a?: Agent) => <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", a?.alive ? ((a.busy || 0) > 0 ? "bg-fg live-dot" : "bg-success") : "bg-danger")} />;
  return (
    <div className="shrink-0 border-b border-border bg-surface" data-profile-strip>
      <div className="flex h-9 items-center gap-3 overflow-x-auto px-4 text-[12px]">
        <button type="button" className="shrink-0 text-label text-fg hover:underline underline-offset-2" onClick={() => setOpen((v) => !v)} aria-expanded={open}>Profile {r.name}</button>
        {r.error ? <span className="text-danger">{r.error}</span> : (
          <>
            <span className="flex shrink-0 items-center gap-1.5 text-muted">{dot(live(r.lead?.agent_id))}lead <span className="text-fg">{agentLabel(r.lead?.adapter)}{r.lead?.model ? ` ${r.lead.model}` : ""}{r.lead?.effort ? ` ${r.lead.effort}` : ""}</span></span>
            <span className="shrink-0 text-muted">chat <span className="text-fg">{r.chat?.model || "default"}{r.chat?.effort ? ` ${r.chat.effort}` : ""}</span></span>
            {r.coders.map((c) => {
              const a = live(c.agent_id);
              return (
                <span key={c.key} className="flex shrink-0 items-center gap-1.5 text-muted" data-strip-coder={c.agent_id}>
                  {dot(a)}
                  <span className="text-fg">{agentLabel(c.adapter)}{c.model ? ` ${c.model}` : ""}{c.effort ? ` ${c.effort}` : ""}</span>
                  {c.hub ? <span className="num">×1</span> : <Stepper value={c.count} onChange={(n) => setCount(c, n)} label={`${agentLabel(c.adapter)} lanes`} />}
                  {a?.alive ? <span className="num text-meta">{a.busy || 0} busy</span> : null}
                </span>
              );
            })}
          </>
        )}
        <span className="flex-1" />
        <span className="shrink-0 text-meta">{view.locked ? "fixed since the project started" : "fixed once the first task starts"}</span>
      </div>
      {open ? <div className="px-4 pb-3"><ProfileCard p={r} agents={agents} onCount={setCount} idle="lanes start within a minute" /></div> : null}
    </div>
  );
}
