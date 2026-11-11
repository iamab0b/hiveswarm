import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { Task } from "@/lib/types";
import { agentLabel, cn, sessionOf } from "@/lib/utils";
import { Button } from "./ui/button";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from "./ui/dialog";
import { Input, Label, Select, Switch, Textarea } from "./ui/fields";
import { act } from "./actions";
import { KEY_HELP } from "@/lib/keys";

const PERMS = [
  { value: "auto", label: "auto", hint: "low-risk approved, risky asks you" },
  { value: "acceptEdits", label: "acceptEdits", hint: "edits allowed, commands ask" },
  { value: "bypass", label: "bypass", hint: "never asks (sandbox only)" },
];

/** Which agents may take a project's work: toggle chips over the live agents, saved to projects.<name>.agents. */
export function RosterPicker({ project }: { project: string }) {
  const agents = useStore((s) => s.agents);
  const [roster, setRoster] = useState<string[] | null>(null);
  useEffect(() => {
    let alive = true;
    setRoster(null);
    api.projectSettings(project).then((r) => alive && setRoster(r.agents)).catch(() => alive && setRoster([]));
    return () => { alive = false; };
  }, [project]);
  if (roster === null) return null;
  const names = Array.from(new Set([...agents.filter((a) => !a.profile).map((a) => a.agent_id), ...roster]));
  const save = async (next: string[]) => {
    setRoster(next);
    try {
      await api.setProjectSettings(project, { agents: next });
    } catch (e) {
      toast.error(String((e as Error).message));
    }
  };
  const toggle = (name: string) => save(roster.includes(name) ? roster.filter((n) => n !== name) : [...roster, name]);
  return (
    <div data-roster>
      <Label hint={roster.length ? "only these take this project's work" : "any agent"}>Agents for {project}</Label>
      <div className="flex flex-wrap gap-1.5">
        <button type="button" onClick={() => save([])} className={cn("h-7 rounded-md border px-2.5 text-[12px] transition-colors duration-150", !roster.length ? "border-fg bg-fg text-bg" : "border-border text-muted hover:text-fg")}>any</button>
        {names.map((n) => {
          const a = agents.find((x) => x.agent_id === n);
          const on = roster.includes(n);
          return (
            <button key={n} type="button" onClick={() => toggle(n)} className={cn("h-7 rounded-md border px-2.5 text-[12px] transition-colors duration-150", on ? "border-fg bg-fg text-bg" : "border-border text-muted hover:text-fg", a && !a.alive && "line-through")}>
              {agentLabel(n)}{a?.model ? <span className={cn("ml-1", on ? "opacity-70" : "text-dim")}>{a.model}{a.effort ? ` ${a.effort}` : ""}</span> : null}
            </button>
          );
        })}
      </div>
    </div>
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
  onConfirm: () => void | Promise<unknown>;
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


export function HelpDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl" data-help>
        <DialogHeader title="Keys" description="A workspace is switched with a key or from the Control tab's project list. Every other key goes to the terminal that has the focus." />
        <DialogBody className="grid gap-1.5">
          {KEY_HELP.map(([k, v]) => (
            <div key={k} className="grid grid-cols-[190px_1fr] items-center gap-3 text-[12px]"><span><kbd className="key">{k}</kbd></span><span className="text-muted">{v}</span></div>
          ))}
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
