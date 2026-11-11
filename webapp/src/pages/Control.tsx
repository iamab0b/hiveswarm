import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ChevronDown, ChevronRight, Loader2, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { Plan, ProjectSettings, Subscription, Task } from "@/lib/types";
import { age, agentLabel, cn, isLive, isTerminal, loudAttention, roleOf, sessionOf } from "@/lib/utils";
import { AgentDot, EmptyState, PageHeader } from "@/components/bits";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from "@/components/ui/dialog";
import { Input, Label, Select, Tip } from "@/components/ui/fields";
import { Markdown } from "@/components/ui/markdown";
import { DeleteProjectDialog, RosterPicker } from "@/components/dialogs";
import { NO_PROFILE, ProfilePicker, ProfilesSection } from "@/components/Profiles";
import { StandingOrders } from "@/components/StandingOrders";
import { DeferredList } from "@/components/Deferred";
import { AgentsSection } from "@/components/AgentCards";
import { FeedPanel, HistoryTable, StatsPanel } from "@/components/Activity";

const SECTIONS = [["projects", "Projects"], ["subscriptions", "Subscriptions"], ["profiles", "Profiles"], ["agents", "Agents"], ["activity", "Activity"]] as const;

/** What a project is doing, in a few words, from its tasks. */
function projectStatus(tasks: Task[]): { text: string; needs: number; live: number } {
  const live = tasks.filter((t) => isLive(t));
  const lead = live.find((t) => roleOf(t) === "lead");
  const agents = live.filter((t) => roleOf(t) === "session" || roleOf(t) === "task");
  const needs = live.filter((t) => roleOf(t) !== "chat" && loudAttention(t)).length;
  const parts: string[] = [];
  if (lead) parts.push(`lead ${sessionOf(lead).turn === "working" ? "working" : sessionOf(lead).tmux ? "idle" : "starting"}`);
  if (agents.length) parts.push(`${agents.length} agent${agents.length === 1 ? "" : "s"} at work`);
  if (!parts.length) parts.push(tasks.some((t) => roleOf(t) !== "chat") ? "idle" : "not started");
  return { text: parts.join(" · "), needs, live: agents.length + (lead ? 1 : 0) };
}

function PlanBlock({ project }: { project: string }) {
  const [plan, setPlan] = useState<Plan | null | undefined>(undefined);
  const [open, setOpen] = useState(false);
  useEffect(() => { api.projectPlan(project).then((r) => setPlan(r.plan)).catch(() => setPlan(null)); }, [project]);
  return (
    <div className="grid gap-1.5" data-plan>
      <button type="button" onClick={() => setOpen((v) => !v)} className="text-label flex items-center gap-2 text-left">
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} Plan
        {plan ? <span className="num text-meta">saved by {plan.updated_by || "?"} · {age(plan.updated_at)} ago</span> : null}
      </button>
      {open ? (plan ? <div className="rounded-md border border-border bg-surface-2 px-3 py-2"><Markdown text={plan.text} className="text-[12.5px]" /></div>
        : <div className="rounded-md border border-dashed border-border px-2.5 py-2 text-[12px] text-dim">No plan saved yet. The lead saves one after planning; ask the chat agent to show or change it.</div>) : null}
    </div>
  );
}

/** Everything about a project that is not a terminal: its profile and lanes, who may work on it, its ruleset, its
 *  plan, its standing orders, what agents deferred, where its files are. */
function ProjectDialog({ name, open, onOpenChange, onDelete }: { name: string | null; open: boolean; onOpenChange: (o: boolean) => void; onDelete: (name: string) => void }) {
  const projects = useStore((s) => s.projects);
  const local = useStore((s) => s.local);
  const loadProjects = useStore((s) => s.loadProjects);
  const [profile, setProfile] = useState<string>(NO_PROFILE);
  const [settings, setSettings] = useState<ProjectSettings | null>(null);
  const [syncing, setSyncing] = useState(false);
  const onProfile = useCallback((v: string) => { setProfile(v); loadProjects(); }, [loadProjects]);
  useEffect(() => {
    if (!open || !name) return;
    setSettings(null);
    api.projectSettings(name).then(setSettings).catch(() => undefined);
  }, [open, name]);
  if (!name) return null;
  const proj = projects[name];
  const copy = proj?.local || null;
  const setRuleset = async (body: { ruleset?: string; ruleset_intensity?: string }) => {
    try {
      setSettings(await api.setProjectSettings(name, body));
      loadProjects();
    } catch (e) {
      toast.error(String((e as Error).message));
    }
  };
  const openFolder = async () => {
    try {
      const r = await api.openLocal(name);
      if (!r.ok) toast.warning(r.reason || "could not open the folder");
    } catch (e) {
      toast.error(String((e as Error).message));
    }
  };
  const syncNow = async () => {
    setSyncing(true);
    try {
      const r = await api.syncLocal(name);
      if (r.ok) toast.success(r.note || "up to date");
      else toast.warning(r.reason || "could not sync");
      await loadProjects();
    } catch (e) {
      toast.error(String((e as Error).message));
    } finally {
      setSyncing(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent wide data-project-dialog={name}>
        <DialogHeader title={name} description="The project's swarm and rules. Its agents live in its workspace; the chat agent there is how you follow and steer the lead." />
        <DialogBody className="grid max-h-[72vh] gap-6 overflow-y-auto lg:grid-cols-2">
          <div className="grid content-start gap-5">
            <ProfilePicker project={name} value={profile} onChange={onProfile} />
            {profile === NO_PROFILE ? <RosterPicker project={name} /> : null}
            <div>
              <Label hint="what every agent of this project works under">Ruleset</Label>
              <div className="grid grid-cols-2 gap-2">
                <Select value={settings?.ruleset || "craft"} onChange={(v) => setRuleset({ ruleset: v })}
                  options={[{ value: "craft", label: "Craft", hint: "minimal code, real tests, a Changed / Tested / Deferred handoff" }, { value: "off", label: "Off" }]} />
                <Select value={settings?.ruleset_intensity || "standard"} onChange={(v) => setRuleset({ ruleset_intensity: v })}
                  options={[{ value: "standard", label: "standard" }, { value: "strict", label: "strict" }]} className={cn(settings?.ruleset === "off" && "pointer-events-none opacity-60")} />
              </div>
            </div>
            <div>
              <Label>Files</Label>
              <div className="grid gap-1 rounded-md border border-border bg-surface-2 px-3 py-2 text-[12px]">
                <div className="flex items-center gap-2"><span className="w-14 shrink-0 text-dim">hub</span><span className="mono min-w-0 truncate text-fg" title={proj?.repo_path}>{proj?.repo_path}</span></div>
                <div className="flex items-center gap-2">
                  <span className="w-14 shrink-0 text-dim">{copy && copy.host !== local?.hostname ? copy.host : "here"}</span>
                  {copy ? <span className="mono min-w-0 flex-1 truncate text-fg" title={copy.win_path || copy.path}>{copy.path.replace(/^\/home\/[^/]+/, "~")}</span> : <span className="flex-1 text-dim">no local copy yet; the worker makes one within a minute</span>}
                  {copy ? <Button size="xs" variant="ghost" onClick={openFolder}>open folder</Button> : null}
                  <Tip label="Fast-forward this machine's copy from the hub now"><Button size="xs" variant="ghost" onClick={syncNow} disabled={syncing}><RefreshCw className={cn("h-3 w-3", syncing && "animate-spin")} /> sync</Button></Tip>
                </div>
                {copy?.note ? <div className={cn("pl-16", copy.dirty || copy.ahead ? "text-accent" : "text-dim")}>{copy.note}</div> : null}
              </div>
            </div>
          </div>
          <div className="grid content-start gap-5">
            <PlanBlock project={name} />
            <StandingOrders project={name} title="Standing orders for every agent" />
            <DeferredList project={name} />
          </div>
        </DialogBody>
        <DialogFooter className="justify-between">
          <Button variant="ghost" className="hover:text-danger" onClick={() => { onOpenChange(false); onDelete(name); }}>Remove project…</Button>
          <Button variant="primary" onClick={() => onOpenChange(false)}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function NewProjectDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const projects = useStore((s) => s.projects);
  const loadProjects = useStore((s) => s.loadProjects);
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [profile, setProfile] = useState<string>(NO_PROFILE);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!open) return;
    setName("");
    api.settings().then((s) => setProfile(s.default_profile || NO_PROFILE)).catch(() => setProfile(NO_PROFILE));
  }, [open]);
  const ok = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(name) && !(name in projects);
  const create = async () => {
    setBusy(true);
    try {
      await api.createProject(name);
      if (profile !== NO_PROFILE) await api.setProjectSettings(name, { profile });
      api.materialize().catch(() => undefined);
      await loadProjects();
      onOpenChange(false);
      nav(`/p/${name}`);
    } catch (e) {
      toast.error(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-new-project-dialog>
        <DialogHeader title="New project" description="An empty repository on the hub with a workspace of its own. Its chat agent opens there; tell it what to build. The chat agent at Home does the same from a brief." />
        <DialogBody className="grid gap-4">
          <div>
            <Label hint={name && name in projects ? "that name is taken" : "letters, digits, - and _"}>Name</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="orbit" autoFocus data-project-name onKeyDown={(e) => { if (e.key === "Enter" && ok && !busy) create(); }} />
          </div>
          <ProfilePicker project={null} value={profile} onChange={setProfile} />
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={!ok || busy} onClick={create}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}Create and open</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ProjectsSection() {
  const projects = useStore((s) => s.projects);
  const tasks = useStore((s) => s.tasks);
  const loadProjects = useStore((s) => s.loadProjects);
  const nav = useNavigate();
  const [settings, setSettings] = useState<string | null>(null);
  const [del, setDel] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const names = Object.keys(projects);
  const byProject = useMemo(() => {
    const m: Record<string, Task[]> = {};
    for (const t of tasks) (m[t.project] ||= []).push(t);
    return m;
  }, [tasks]);
  return (
    <div data-projects>
      <div className="mb-2 flex items-end justify-between gap-3">
        <div>
          <div className="text-label text-fg">Projects</div>
          <div className="text-[12px] text-muted">Each project has a workspace: its chat agent, its lead, and a tile per agent the lead spawned. Open one here or with its key.</div>
        </div>
        <Button size="sm" variant="secondary" onClick={() => setAdding(true)} data-new-project>new project</Button>
      </div>
      {names.length ? (
        <Card className="divide-y divide-border overflow-hidden">
          {names.map((name, i) => {
            const p = projects[name];
            const st = projectStatus(byProject[name] || []);
            return (
              <div key={name} className="flex min-h-11 items-center gap-3 px-3 py-1.5" data-project-row={name}>
                {i < 9 ? <kbd className="key shrink-0">Ctrl+Shift+{i + 1}</kbd> : <span className="w-[86px] shrink-0" />}
                <button type="button" className="min-w-0 shrink-0 text-left text-[13px] font-medium text-fg hover:underline underline-offset-2" onClick={() => nav(`/p/${name}`)}>{name}</button>
                <span className="shrink-0 text-[12px] text-muted">{p.profile ? `profile ${p.profile}` : "any agent"}</span>
                <span className="flex min-w-0 flex-1 items-center gap-2 truncate text-[12px] text-muted">
                  <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", st.live ? "bg-fg live-dot" : "bg-dim")} />
                  <span className="truncate">{st.text}</span>
                  {p.paused ? <span className="shrink-0 text-accent">paused</span> : null}
                  {st.needs ? <span className="shrink-0 font-medium text-accent" data-needs-you>{st.needs} need{st.needs === 1 ? "s" : ""} you</span> : null}
                  {p.deferred_open ? <span className="shrink-0 text-dim">{p.deferred_open} deferred</span> : null}
                </span>
                <Button size="xs" variant="ghost" onClick={() => setSettings(name)} data-project-settings={name}>settings</Button>
                <Button size="xs" variant="secondary" onClick={() => nav(`/p/${name}`)} data-open-project={name}>open</Button>
              </div>
            );
          })}
        </Card>
      ) : (
        <Card><EmptyState className="min-h-[120px]" title="No projects yet" hint="Paste a brief to the chat agent at Home and it starts one, or make an empty one here." action={<Button size="sm" variant="secondary" onClick={() => setAdding(true)}>new project</Button>} /></Card>
      )}
      <NewProjectDialog open={adding} onOpenChange={setAdding} />
      <ProjectDialog name={settings} open={!!settings} onOpenChange={(o) => !o && setSettings(null)} onDelete={setDel} />
      <DeleteProjectDialog name={del} repoPath={del ? projects[del]?.repo_path : undefined} localPath={del ? projects[del]?.local?.path : null} open={!!del} onOpenChange={(o) => !o && setDel(null)} onDeleted={async () => { await loadProjects(); api.materialize().catch(() => undefined); }} />
    </div>
  );
}

function SubscriptionsSection() {
  const agents = useStore((s) => s.agents);
  const [subs, setSubs] = useState<Subscription[] | null>(null);
  const [host, setHost] = useState("");
  useEffect(() => {
    let alive = true;
    const load = () => api.subscriptions().then((r) => { if (alive) { setSubs(r.subscriptions); setHost(r.host); } }).catch(() => alive && setSubs([]));
    load();
    const i = setInterval(load, 30000);
    return () => { alive = false; clearInterval(i); };
  }, []);
  const hub = agents.filter((a) => a.local);
  const lanes = (s: Subscription) => agents.filter((a) => (a.provider || a.agent_id) === s.id && a.alive && a.profile !== "_chat").reduce((n, a) => n + (a.capacity || 0), 0);
  const shown = (subs || []).filter((s) => s.installed || s.tables.length || ["claude_code", "codex"].includes(s.id));
  return (
    <div data-subscriptions>
      <div className="mb-2">
        <div className="text-label text-fg">Subscriptions</div>
        <div className="text-[12px] text-muted">The agent CLIs on {host || "this machine"} and whether each is signed in. Every lane of a CLI shares its one sign-in, so its rate limits apply to all of them together.</div>
      </div>
      <Card className="divide-y divide-border overflow-hidden">
        {shown.map((s) => {
          const ok = s.installed && s.signed_in === true;
          const bad = s.installed && s.signed_in === false;
          const n = lanes(s);
          return (
            <div key={s.id} className="flex min-h-9 flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-1.5 text-[12.5px]" data-subscription={s.id}>
              <span className="flex w-[130px] shrink-0 items-center gap-2 font-medium text-fg"><AgentDot agent={s.id} />{s.label}</span>
              <span className="w-[150px] shrink-0 text-muted">{s.plan}</span>
              <span className={cn("flex w-[150px] shrink-0 items-center gap-1.5", bad ? "text-danger" : "text-muted")}>
                <span className={cn("h-1.5 w-1.5 rounded-full", ok ? "bg-success" : bad ? "bg-danger" : "bg-dim")} />
                {!s.installed ? "not installed" : s.signed_in === true ? "signed in" : s.signed_in === false ? "not signed in" : "installed"}
              </span>
              <span className="min-w-0 flex-1 truncate text-muted" title={s.detail}>{s.installed && s.signed_in !== true && s.fix ? <>run <span className="mono text-fg">{s.fix}</span></> : s.detail}</span>
              <span className="num shrink-0 text-meta">{n ? `${n} lane${n === 1 ? "" : "s"}` : s.installed ? "no lanes" : ""}</span>
            </div>
          );
        })}
        {hub.map((a) => (
          <div key={a.agent_id} className="flex min-h-9 flex-wrap items-center gap-x-3 px-3 py-1.5 text-[12.5px]" data-subscription={a.agent_id}>
            <span className="flex w-[130px] shrink-0 items-center gap-2 font-medium text-fg"><AgentDot agent={a.agent_id} />{agentLabel(a.agent_id)}</span>
            <span className="w-[150px] shrink-0 text-muted">local model on the hub</span>
            <span className={cn("flex w-[150px] shrink-0 items-center gap-1.5", a.alive ? "text-muted" : "text-danger")}><span className={cn("h-1.5 w-1.5 rounded-full", a.alive ? "bg-success" : "bg-danger")} />{a.alive ? "up" : "not reachable"}</span>
            <span className="min-w-0 flex-1 truncate text-muted" title={a.where}>{a.model ? <span className="mono text-fg">{a.model}</span> : null}{a.host ? ` on ${a.host}` : ""} · no subscription used</span>
            <span className="num shrink-0 text-meta">1 lane</span>
          </div>
        ))}
        {subs !== null && !shown.length && !hub.length ? <EmptyState className="min-h-[100px]" title="No agent CLI found" hint="Install Claude Code or Codex on this machine and sign in; the worker picks them up." /> : null}
      </Card>
    </div>
  );
}

/** The other tab: everything that is not a terminal. Projects, the subscriptions behind the agents, the profiles
 *  that say which agents a project gets and how many, the agents themselves, and what they have been doing. */
export default function ControlPage() {
  const tasks = useStore((s) => s.tasks);
  const running = tasks.filter((t) => !isTerminal(t.state) && roleOf(t) !== "chat" && isLive(t)).length;
  return (
    <div className="h-full overflow-y-auto" data-control>
      <div className="mx-auto max-w-[1200px] px-6 py-5">
        <PageHeader title="Control" subtitle="Projects, the subscriptions connected, and the agents: which ones a project gets and how many."
          right={(
            <nav className="flex items-center gap-3 text-[12px]">
              {SECTIONS.map(([id, label]) => <a key={id} href={`#${id}`} className="text-muted hover:text-fg" onClick={(e) => { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>{label}</a>)}
              <span className="num text-meta">{running} at work</span>
            </nav>
          )} />
        <div className="grid gap-10">
          <section id="projects"><ProjectsSection /></section>
          <section id="subscriptions"><SubscriptionsSection /></section>
          <section id="profiles"><ProfilesSection /></section>
          <section id="agents">
            <div className="mb-2">
              <div className="text-label text-fg">Agents</div>
              <div className="text-[12px] text-muted">Every agent a worker has registered and the lanes the hub runs itself. An agent's lane count is concurrency in its worker.toml; the worker applies a change without a restart.</div>
            </div>
            <AgentsSection />
          </section>
          <section id="activity" className="grid gap-8">
            <StatsPanel />
            <FeedPanel />
            <HistoryTable />
          </section>
        </div>
      </div>
    </div>
  );
}
