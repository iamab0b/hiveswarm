import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { X } from "lucide-react";
import { api } from "@/lib/api";
import type { Directive } from "@/lib/types";
import { age, cn, short } from "@/lib/utils";
import { Button } from "./ui/button";
import { Input, Label, Switch, Textarea, Tip } from "./ui/fields";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from "./ui/dialog";
import { act } from "./actions";

export function useDirectives(scope: { task_id?: string; project?: string; for_task?: string }, every = 5000) {
  const [items, setItems] = useState<Directive[] | null>(null);
  const key = `${scope.task_id || ""}|${scope.project || ""}|${scope.for_task || ""}`;
  const reload = useCallback(() => {
    if (!scope.task_id && !scope.project && !scope.for_task) return Promise.resolve();
    return api.directives(scope).then(setItems).catch(() => undefined);
  }, [key]);
  useEffect(() => {
    let alive = true;
    const go = () => { if (alive) reload(); };
    go();
    const i = setInterval(go, every);
    return () => { alive = false; clearInterval(i); };
  }, [key, every]);
  return { items: items || [], loaded: items !== null, reload };
}

function lastCheck(d: Directive): { label: string; tone: "good" | "bad" | "dim" } {
  if (!d.check) return { label: "no audit", tone: "dim" };
  if (!d.last_result) return { label: d.fires ? "reminded" : "not yet", tone: "dim" };
  try {
    const r = JSON.parse(d.last_result) as { p?: number; violated?: boolean; at?: number };
    if (r.violated) return { label: `flagged ${Math.round((r.p || 0) * 100)}%`, tone: "bad" };
    return { label: `ok ${Math.round((1 - (r.p || 0)) * 100)}%`, tone: "good" };
  } catch {
    return { label: "checked", tone: "dim" };
  }
}

export function DirectiveRow({ d, currentTask, onRemoved }: { d: Directive; currentTask?: string | null; onRemoved?: () => void }) {
  const c = lastCheck(d);
  return (
    <div className={cn("group rounded-md border border-border bg-surface py-2 pl-3 pr-2.5", d.violations ? "border-l-2 border-l-accent" : "")}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1 text-[12.5px] leading-[18px] text-fg">{d.text}</div>
        <Tip label="Retire this standing order"><Button size="icon-sm" variant="ghost" className="-mr-1 -mt-1 opacity-0 group-hover:opacity-100" onClick={async () => { await act.removeDirective(d.id); onRemoved?.(); }}><X className="h-3.5 w-3.5" /></Button></Tip>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-meta">
        {!d.task_id ? <span>whole project</span> : d.task_id === currentTask ? <span>this lane</span> : <Link to={`/t/${d.task_id}`} className="mono hover:text-fg hover:underline">lane {short(d.task_id)}</Link>}
        <span>· every {d.every_tools} tools / {d.every_minutes} min</span>
        <span>· reminded {d.fires}×</span>
        {d.check ? <span className={cn(c.tone === "bad" && "text-accent", c.tone === "good" && "text-success")}>· last audit{!d.task_id ? " (any lane)" : ""}: {c.label}</span> : <span>· no audit</span>}
        {d.violations ? <span className="text-accent">· {d.violations} flag{d.violations === 1 ? "" : "s"}</span> : null}
        {d.created_by ? <span>· by {d.created_by}</span> : null}
        {d.last_fired_at ? <span>· {age(d.last_fired_at)}</span> : null}
      </div>
    </div>
  );
}

export function AddDirectiveDialog({ open, onOpenChange, taskId, project, onAdded }: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  taskId?: string | null;
  project?: string | null;
  onAdded?: () => void;
}) {
  const [text, setText] = useState("");
  const [tools, setTools] = useState("8");
  const [minutes, setMinutes] = useState("10");
  const [check, setCheck] = useState(true);
  const [scope, setScope] = useState<"task" | "project">(taskId ? "task" : "project");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) { setScope(taskId ? "task" : "project"); } }, [open, taskId]);
  const go = async () => {
    const v = text.trim();
    if (!v) return;
    setBusy(true);
    try {
      const id = await act.addDirective({
        text: v,
        task_id: scope === "task" ? taskId : null,
        project: scope === "project" ? project : null,
        every_tools: Math.max(1, Number(tools) || 8),
        every_minutes: Math.max(1, Number(minutes) || 10),
        check,
      });
      if (id) { setText(""); onAdded?.(); onOpenChange(false); }
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg" onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); go(); } }}>
        <DialogHeader title="New standing order" description="A paramount rule the agent must keep following. Hiveswarm puts it at the top of the prompt, reminds the agent on a cadence, and can audit the agent's recent activity after each reminder." />
        <DialogBody className="grid gap-4">
          <div>
            <Label hint="one or two plain sentences; say what to do instead">The order</Label>
            <Textarea autoFocus rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="Never copy code from a repository we do not have permission to copy from; write it yourself and cite any API you consulted." />
          </div>
          {taskId && project ? (
            <div>
              <Label>Applies to</Label>
              <div className="flex gap-1.5">
                <Button size="sm" variant={scope === "task" ? "primary" : "secondary"} onClick={() => setScope("task")}>this lane only</Button>
                <Button size="sm" variant={scope === "project" ? "primary" : "secondary"} onClick={() => setScope("project")}>every agent in {project}</Button>
              </div>
            </div>
          ) : null}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label hint="Claude Code lanes, mid-turn">Remind every N tool calls</Label>
              <Input type="number" min={1} value={tools} onChange={(e) => setTools(e.target.value)} />
            </div>
            <div>
              <Label hint="any agent, between turns">or every M minutes</Label>
              <Input type="number" min={1} value={minutes} onChange={(e) => setMinutes(e.target.value)} />
            </div>
          </div>
          <Switch checked={check} onChange={setCheck} label="After each reminder, check the agent's recent activity against the order and flag a likely violation to the inbox" />
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} kbd="esc">Cancel</Button>
          <Button variant="primary" onClick={go} disabled={busy || !text.trim()} kbd="⌘⏎">Set order</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function StandingOrders({ taskId, project, compact, title = "Standing orders" }: {
  taskId?: string | null;
  project?: string | null;
  compact?: boolean;
  title?: string;
}) {
  const scope = taskId ? { for_task: taskId } : { project: project || undefined };
  const { items, reload } = useDirectives(scope);
  const [add, setAdd] = useState(false);
  const own = items.filter((d) => !taskId || d.task_id === taskId);
  const inherited = taskId ? items.filter((d) => d.task_id !== taskId) : [];
  return (
    <div className={cn("grid gap-1.5", compact && "text-[12px]")}>
      <div className="text-label flex items-center gap-2">
        {title}
        <span className="num text-dim">{items.length || ""}</span>
        <span className="flex-1" />
        <Tip label="Add a standing order"><Button size="xs" variant="ghost" onClick={() => setAdd(true)}>add</Button></Tip>
      </div>
      {items.length ? (
        <div className="grid gap-1.5">
          {own.map((d) => <DirectiveRow key={d.id} d={d} currentTask={taskId} onRemoved={reload} />)}
          {inherited.map((d) => <DirectiveRow key={d.id} d={d} currentTask={taskId} onRemoved={reload} />)}
        </div>
      ) : (
        <div className="rounded-md border border-dashed border-border px-2.5 py-2 text-[12px] text-dim">
          {taskId ? "None. The lead sets one when a lane must not drift from a rule (licensing, scope, safety); you can add one too." : `None for ${project || "this project"}. A project-wide order reaches every current and future agent in it.`}
        </div>
      )}
      <AddDirectiveDialog open={add} onOpenChange={setAdd} taskId={taskId} project={project} onAdded={reload} />
    </div>
  );
}
