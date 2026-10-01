import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Loader2, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { Task } from "@/lib/types";
import { agentLabel, sessionOf } from "@/lib/utils";
import { Button } from "./ui/button";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from "./ui/dialog";
import { Input, Label, Segmented, Select, Switch, Textarea } from "./ui/fields";
import { act } from "./actions";

type RunMode = "tasks" | "lead" | "swarm" | "session";
const NEW = "__new__";

const PERMS = [
  { value: "auto", label: "auto", hint: "low-risk approved, risky asks you" },
  { value: "acceptEdits", label: "acceptEdits", hint: "edits allowed, commands ask" },
  { value: "bypass", label: "bypass", hint: "never asks (sandbox only)" },
];

export function GoalDialog({ open, onOpenChange, defaultProject }: { open: boolean; onOpenChange: (o: boolean) => void; defaultProject?: string | null }) {
  const projects = useStore((s) => s.projects);
  const agents = useStore((s) => s.agents);
  const loadProjects = useStore((s) => s.loadProjects);
  const nav = useNavigate();
  const names = Object.keys(projects);
  const [project, setProject] = useState<string>(defaultProject && projects[defaultProject] ? defaultProject : names[0] || NEW);
  const [newName, setNewName] = useState("");
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState<RunMode>("tasks");
  const [agent, setAgent] = useState("claude_code");
  const [perm, setPerm] = useState("auto");
  const [count, setCount] = useState(1);
  const [review, setReview] = useState(false);
  const [planning, setPlanning] = useState(false);
  const [plan, setPlan] = useState<{ spec: string; acceptance: string; include: boolean }[] | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const sessionAgents = useMemo(() => {
    const names = agents.filter((a) => a.alive && (a.capabilities || []).includes("sessions")).map((a) => a.agent_id);
    const uniq = Array.from(new Set(names.length ? names : ["claude_code"]));
    uniq.sort((a, b) => (a === "claude_code" ? -1 : b === "claude_code" ? 1 : a.localeCompare(b)));
    return uniq;
  }, [agents]);

  useEffect(() => {
    if (open) {
      loadProjects();
      setPlan(null);
      setPlanning(false);
      setSubmitting(false);
      if (defaultProject && projects[defaultProject]) setProject(defaultProject);
    }
  }, [open]);
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (!names.length) setProject(NEW);
    else if (project === NEW && !touched) setProject(defaultProject && projects[defaultProject] ? defaultProject : names[0]);
    else if (project !== NEW && !projects[project]) setProject(names[0]);
  }, [names.length]);
  useEffect(() => {
    if (mode === "lead") setAgent("claude_code");
  }, [mode]);

  const isNew = project === NEW;
  const nameOk = !isNew || /^[A-Za-z0-9_-]{1,64}$/.test(newName);
  const canGo = goal.trim().length > 0 && nameOk && !planning && !submitting;

  const resolveProject = async (): Promise<{ name: string; created: boolean } | null> => {
    if (!isNew) return { name: project, created: false };
    if (projects[newName]) return { name: newName, created: false };
    try {
      const r = await api.createProject(newName);
      toast.success(`Created project ${r.name} at ${r.repo_path}`);
      await loadProjects();
      return { name: r.name, created: true };
    } catch (e) {
      toast.error(`couldn't create project: ${String((e as Error).message)}`);
      return null;
    }
  };

  const doPlan = async () => {
    setPlanning(true);
    try {
      const r = await api.plan({ goal, project: isNew ? newName : project, new_repo: isNew });
      if (r.error || !r.tasks?.length) {
        toast.warning(`planning failed (${r.error || "no tasks"}); sending the goal as one item`);
        setPlan([{ spec: goal, acceptance: "", include: true }]);
      } else {
        setPlan(r.tasks.map((t) => ({ spec: t.spec, acceptance: t.acceptance || "", include: true })));
      }
    } catch (e) {
      toast.error(String((e as Error).message));
    } finally {
      setPlanning(false);
    }
  };

  const submit = async () => {
    if (!canGo) return;
    if (review && (mode === "tasks" || mode === "swarm") && !plan) {
      await doPlan();
      return;
    }
    setSubmitting(true);
    try {
      const p = await resolveProject();
      if (!p) return;
      if (mode === "session") {
        const r = await api.newSession({ project: p.name, spec: goal, agent, permission_mode: perm, count });
        toast.success(`Opened ${r.ids.length} session${r.ids.length === 1 ? "" : "s"} on ${agentLabel(agent)}`);
        onOpenChange(false);
        if (r.ids.length === 1) nav(`/sessions/${r.ids[0]}`);
        return;
      }
      if (mode === "lead") {
        const r = await api.newSession({ project: p.name, spec: goal, agent: "claude_code", permission_mode: perm, lead: true });
        toast.success(`Lead ${r.id.slice(0, 8)} is starting — it plans and dispatches the swarm`);
        onOpenChange(false);
        nav(`/sessions/${r.id}`);
        return;
      }
      let items: { spec: string; acceptance: string | null }[];
      if (plan) {
        items = plan.filter((x) => x.include && x.spec.trim()).map((x) => ({ spec: x.spec.trim(), acceptance: x.acceptance.trim() || null }));
        if (!items.length) {
          toast.warning("no tasks selected");
          return;
        }
      } else {
        toast("Planning with Claude Code… the agents start as soon as the plan is ready", { duration: 8000 });
        const r = await api.plan({ goal, project: p.name, new_repo: p.created });
        items = r.error || !r.tasks?.length ? [{ spec: goal, acceptance: null }] : r.tasks.map((t) => ({ spec: t.spec, acceptance: t.acceptance || null }));
        if (r.error) toast.warning(`planning failed (${r.error}); sending the goal as one item`);
        if (p.created && items.length > 1) {
          items = items.slice(0, 1);
          toast("new project: one agent builds the first version (parallel agents on an empty repo would clash)");
        }
      }
      let n = 0;
      for (const it of items) {
        if (mode === "swarm") {
          const r = await api.newSession({ project: p.name, spec: it.spec, acceptance: it.acceptance, agent, permission_mode: perm });
          n += r.ids.length;
        } else {
          await api.addTask(p.name, it.spec, it.acceptance);
          n += 1;
        }
      }
      toast.success(mode === "swarm" ? `Opened ${n} session${n === 1 ? "" : "s"}` : `Enqueued ${n} task${n === 1 ? "" : "s"}`);
      onOpenChange(false);
      nav("/");
    } catch (e) {
      toast.error(String((e as Error).message));
    } finally {
      setSubmitting(false);
    }
  };

  const showSessionOpts = mode !== "tasks";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent wide={!!plan}>
        <DialogHeader title="New goal" description="Say what you want and choose how the swarm runs it." />
        <DialogBody className="max-h-[70vh] overflow-y-auto">
          <div className="grid gap-4" onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit(); }}>
            <div className="grid grid-cols-[1fr_1fr] gap-3">
              <div>
                <Label>Project</Label>
                <Select value={project} onChange={(v) => { setTouched(true); setProject(v); }} options={[...names.map((n) => ({ value: n, label: n })), { value: NEW, label: "+ New project…" }]} />
              </div>
              {isNew ? (
                <div>
                  <Label hint="letters, digits, - and _">Name</Label>
                  <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="my-app" autoFocus />
                </div>
              ) : <div />}
            </div>
            <div>
              <Label hint="⌘⏎ to start">Goal</Label>
              <Textarea value={goal} onChange={(e) => setGoal(e.target.value)} rows={5} placeholder="Add rate limiting to the API: a token bucket per user, config in settings.toml, tests for the limits" autoFocus={!isNew} />
            </div>
            <div>
              <Label>Run as</Label>
              <Segmented<RunMode>
                value={mode}
                onChange={(m) => { setMode(m); setPlan(null); }}
                options={[
                  { value: "tasks", label: "Tasks", hint: "plan it, agents work headless" },
                  { value: "lead", label: "Lead", hint: "Claude Code commands the swarm; you steer it" },
                  { value: "swarm", label: "Swarm", hint: "plan it, one live session per subtask" },
                  { value: "session", label: "Session", hint: "one live session with this prompt" },
                ]}
              />
            </div>
            {showSessionOpts ? (
              <div className="grid grid-cols-[1fr_1fr_100px] gap-3">
                <div>
                  <Label>Agent</Label>
                  <Select value={mode === "lead" ? "claude_code" : agent} onChange={setAgent} options={sessionAgents.map((a) => ({ value: a, label: agentLabel(a) }))} />
                </div>
                <div>
                  <Label>Permissions</Label>
                  <Select value={perm} onChange={setPerm} options={PERMS} />
                </div>
                {mode === "session" ? (
                  <div>
                    <Label hint="parallel attempts">Copies</Label>
                    <Input type="number" min={1} max={12} value={count} onChange={(e) => setCount(Math.max(1, Math.min(12, Number(e.target.value) || 1)))} />
                  </div>
                ) : <div />}
              </div>
            ) : null}
            {mode === "tasks" || mode === "swarm" ? (
              <Switch checked={review} onChange={(v) => { setReview(v); if (!v) setPlan(null); }} label="Let me review the plan before anything starts" />
            ) : null}
            {planning ? (
              <div className="flex items-center gap-2 rounded-md border border-border bg-surface-2 px-3 py-2 text-[12px] text-muted">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Planning with Claude Code, 20–60 seconds
              </div>
            ) : null}
            {plan ? (
              <div className="grid gap-2">
                <div className="text-[12px] text-muted">{plan.filter((p) => p.include).length} of {plan.length} planned {mode === "swarm" ? "sessions" : "tasks"} selected. Edit inline; untick what you don't want.</div>
                {plan.map((p, i) => (
                  <div key={i} className="rounded-md border border-border p-3">
                    <div className="mb-2 flex items-center justify-between">
                      <Switch checked={p.include} onChange={(v) => setPlan(plan.map((x, j) => (j === i ? { ...x, include: v } : x)))} label={`#${i + 1}`} />
                      <Button size="xs" variant="ghost" onClick={() => setPlan(plan.filter((_, j) => j !== i))}><Trash2 className="h-3 w-3" /></Button>
                    </div>
                    <Textarea value={p.spec} rows={3} onChange={(e) => setPlan(plan.map((x, j) => (j === i ? { ...x, spec: e.target.value } : x)))} />
                    <Input className="mono mt-2 text-[12px]" value={p.acceptance} placeholder="acceptance command (blank = soft verify)" onChange={(e) => setPlan(plan.map((x, j) => (j === i ? { ...x, acceptance: e.target.value } : x)))} />
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={!canGo} onClick={submit}>
            {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {mode === "lead" ? "Start the lead" : mode === "session" ? (count > 1 ? `Open ${count} sessions` : "Open session") : review && !plan ? "Plan" : mode === "swarm" ? "Plan & open sessions" : "Start"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function RetryDialog({ task, lastError, open, onOpenChange }: { task: Task | null; lastError?: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [spec, setSpec] = useState("");
  const [acc, setAcc] = useState("");
  useEffect(() => {
    if (open && task) {
      setSpec(task.kind === "session" ? "" : task.spec);
      setAcc(task.acceptance || "");
    }
  }, [open, task?.id]);
  if (!task) return null;
  const isSession = task.kind === "session";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader title={isSession ? `Reopen session ${task.id.slice(0, 8)}` : `Retry ${task.id.slice(0, 8)}`}
          description={isSession ? "A Claude Code session resumes with its previous conversation and the files as it left them." : "The task goes back to the queue; edit the spec if the last attempt missed the point."} />
        <DialogBody className="grid gap-3">
          {lastError ? (
            <div>
              <Label>Last verifier output</Label>
              <pre className="mono max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-border border-l-2 border-l-danger bg-surface-2 p-2 text-muted">{lastError.slice(-2000)}</pre>
            </div>
          ) : null}
          <div>
            <Label>{isSession ? "New instructions (optional)" : "Spec"}</Label>
            <Textarea rows={isSession ? 3 : 6} value={spec} onChange={(e) => setSpec(e.target.value)} placeholder={isSession ? "Pick up where you left off" : undefined} />
          </div>
          <div>
            <Label hint="exits 0 when done">Acceptance</Label>
            <Input className="mono text-[12px]" value={acc} onChange={(e) => setAcc(e.target.value)} placeholder="python3 -m pytest -q" />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" onClick={async () => { await act.retry(task.id, isSession ? (spec.trim() || null) : spec, acc.trim() || null); onOpenChange(false); }}>
            {isSession ? "Reopen" : "Retry"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ContinueDialog({ task, open, onOpenChange }: { task: Task | null; open: boolean; onOpenChange: (o: boolean) => void }) {
  const agents = useStore((s) => s.agents);
  const current = task ? sessionOf(task).agent : null;
  const candidates = useMemo(() => {
    const list = agents.filter((a) => a.alive && (a.capabilities || []).includes("sessions")).map((a) => a.agent_id);
    const others = Array.from(new Set(list)).filter((a) => a !== current);
    return others.length ? others : ["codex", "claude_code"].filter((a) => a !== current);
  }, [agents, current]);
  const [agent, setAgent] = useState(candidates[0] || "codex");
  const [perm, setPerm] = useState("auto");
  useEffect(() => { if (open) setAgent(candidates[0] || "codex"); }, [open, candidates.join(",")]);
  if (!task) return null;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader title={`Continue ${task.id.slice(0, 8)} on another agent`} description="Its worktree is saved first; the new agent starts from those changes with a summary of what happened. This session closes." />
        <DialogBody className="grid grid-cols-2 gap-3">
          <div>
            <Label>Agent</Label>
            <Select value={agent} onChange={setAgent} options={candidates.map((a) => ({ value: a, label: agentLabel(a) }))} />
          </div>
          <div>
            <Label>Permissions</Label>
            <Select value={perm} onChange={setPerm} options={PERMS} />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" onClick={async () => { await act.continueOn(task.id, agent, perm); onOpenChange(false); }}>Continue</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ConfirmDialog({ open, onOpenChange, title, body, confirmLabel = "Confirm", danger, onConfirm }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  body?: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void | Promise<void>;
}) {
  const go = async () => { await onConfirm(); onOpenChange(false); };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md" onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); go(); } }} onOpenAutoFocus={(e) => { e.preventDefault(); (e.currentTarget as HTMLElement | null)?.querySelector<HTMLButtonElement>("[data-confirm]")?.focus(); }}>
        <DialogHeader title={title} description={body} />
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} kbd="esc">Cancel</Button>
          <Button data-confirm variant={danger ? "destructive-fill" : "primary"} onClick={go} kbd="⏎">{confirmLabel}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}


const KEYS: [string, string][] = [
  ["g", "new goal"], ["⌘K", "search / jump anywhere"], ["i", "inbox drawer"], ["n", "next thing that needs you"],
  ["1–7", "switch page (Lead, Swarm, Inbox, Hive, Tasks, Stats, Agents)"], ["?", "this help"],
];
const SESSION_KEYS: [string, string][] = [
  ["y / d", "approve / deny what it is asking"], ["1–9", "pick an option when it asks a question"], ["t", "type a message (esc leaves the box)"],
  ["F", "finish: commit, push, verify, close the terminal"], ["o", "continue on another agent"], ["c", "cancel (kills it)"],
  ["click", "the terminal to type straight into the agent — it is the real one"], ["⇧esc", "leave the terminal (plain esc goes to the agent)"],
  ["select", "copies (also ctrl+shift+c, or ctrl+c while something is selected)"], ["right-click", "pastes (also ctrl+shift+v or shift+insert)"],
];

export function HelpDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader title="Keyboard" description="Everything is also clickable; these are the fast paths." />
        <DialogBody className="grid gap-5 sm:grid-cols-2">
          <div>
            <div className="text-label mb-2">Anywhere</div>
            <div className="grid gap-1.5">
              {KEYS.map(([k, v]) => (<div key={k} className="flex items-center gap-3 text-[12px]"><kbd className="key min-w-[44px]">{k}</kbd><span className="text-muted">{v}</span></div>))}
            </div>
          </div>
          <div>
            <div className="text-label mb-2">On a session</div>
            <div className="grid gap-1.5">
              {SESSION_KEYS.map(([k, v]) => (<div key={k} className="flex items-center gap-3 text-[12px]"><kbd className="key min-w-[44px]">{k}</kbd><span className="text-muted">{v}</span></div>))}
            </div>
          </div>
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}


export function DeleteProjectDialog({ name, repoPath, localPath, open, onOpenChange, onDeleted }: {
  name: string | null;
  repoPath?: string;
  localPath?: string | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onDeleted?: () => void;
}) {
  const [purge, setPurge] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) setPurge(false); }, [open]);
  const go = async () => {
    if (!name) return;
    setBusy(true);
    try {
      const r = await api.deleteProject(name, purge);
      toast.success(`Removed ${name}` + (r.tasks_removed ? ` and ${r.tasks_removed} task${r.tasks_removed === 1 ? "" : "s"}` : "") + (purge ? (r.repo_removed ? "; its files are gone" : "; the repository was outside Hiveswarm's folder and was kept") : ""));
      onOpenChange(false);
      onDeleted?.();
    } catch (e) {
      toast.error(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader title={`Remove ${name || "project"} from Hiveswarm?`} description="Its tasks, sessions, logs, standing orders and hub worktrees are removed. Running agents are stopped." />
        <DialogBody className="grid gap-3">
          <Switch checked={purge} onChange={setPurge} label="Also delete the project's files" />
          <div className="rounded-md border border-border bg-surface-2 px-3 py-2 text-[12px] text-muted">
            {purge ? (
              <>
                <div>The repository on the hub is deleted{repoPath ? <>: <span className="mono text-fg">{repoPath}</span></> : ""}.</div>
                {localPath ? <div className="mt-1">This machine's copy moves to <span className="mono text-fg">~/hiveswarm/.trash</span> rather than being deleted.</div> : null}
              </>
            ) : (
              <>
                <div>Files stay where they are{repoPath ? <>: <span className="mono text-fg">{repoPath}</span> on the hub</> : ""}{localPath ? <> and <span className="mono text-fg">{localPath}</span> here</> : ""}.</div>
                <div className="mt-1">Only Hiveswarm forgets the project.</div>
              </>
            )}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} kbd="esc">Cancel</Button>
          <Button variant="destructive-fill" onClick={go} disabled={busy || !name}><Trash2 className="h-3.5 w-3.5" /> {purge ? "Remove and delete files" : "Remove from Hiveswarm"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
