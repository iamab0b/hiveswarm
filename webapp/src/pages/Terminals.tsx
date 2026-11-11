import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, api, leadApi } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { Task } from "@/lib/types";
import { HOME, isLive, isTerminal, loudAttention, roleOf } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ConfirmDialog, ContinueDialog } from "@/components/dialogs";
import { Workspace, isDoneForStrip } from "@/components/Workspace";
import { act } from "@/components/actions";

const QUEUED = ["pending", "classifying", "classified", "assigned"];

function useNow() {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const i = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(i);
  }, []);
  return now;
}

function Placeholder({ title, hint, action }: { title: string; hint?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-1 bg-surface px-8 text-center" data-placeholder>
      <div className="text-[13px] font-medium text-fg">{title}</div>
      {hint ? <div className="max-w-md text-[12px] leading-[18px] text-muted">{hint}</div> : null}
      {action ? <div className="mt-3 flex items-center gap-2">{action}</div> : null}
    </div>
  );
}

/**
 * The chat agent that belongs to no project. A window that opens for the first time asks for a fresh one (whatever
 * was said to the last one is gone); after that the same one stays until it starts a project and moves there, or
 * ends, and then the next one is asked for.
 */
function useHomeChat(active: boolean) {
  const local = useStore((s) => s.local);
  const boot = useStore((s) => s.bootstrapped);
  const homeChat = useStore((s) => s.homeChat);
  const setHomeChat = useStore((s) => s.setHomeChat);
  const t = useStore((s) => (s.homeChat ? s.taskById[s.homeChat] : undefined));
  const asking = useRef(false);
  const gotAt = useRef(0);
  const quick = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [stuck, setStuck] = useState(false);

  const ask = useCallback(async (fresh: boolean) => {
    if (asking.current) return;
    asking.current = true;
    try {
      const r = await api.chatHome(useStore.getState().local?.hostname || null, fresh);
      gotAt.current = Date.now();
      setHomeChat(r.id);
      setError(null);
      try { sessionStorage.setItem("hm.home", "1"); } catch { /* no storage */ }
    } catch (e) {
      setError(e instanceof ApiError && e.status === 404
        ? "The hub runs an older Hiveswarm that has no chat agents. Install 0.4 on the hub, then try again."
        : String((e as Error).message || e));
    } finally {
      asking.current = false;
    }
  }, [setHomeChat]);

  useEffect(() => {
    if (!active || !local || !boot || stuck) return;
    if (!homeChat) {
      let seen = false;
      try { seen = sessionStorage.getItem("hm.home") === "1"; } catch { /* no storage */ }
      ask(!seen);
      return;
    }
    const age = Date.now() - gotAt.current;
    if (!t) {
      if (age > 8000) ask(false);  // it never showed up, or it was removed
      return;
    }
    if (t.project !== HOME) return;  // it joined a project: the page follows it there
    if (isTerminal(t.state)) {
      // one that ends right after starting (no sign-in, a broken install) must not be restarted in a loop
      if (t.state === "failed" && age < 20000) quick.current += 1; else quick.current = 0;
      if (quick.current >= 2) { setStuck(true); return; }
      ask(false);
    }
  }, [active, local, boot, homeChat, t, ask, stuck]);

  return { t, error, stuck, retry: () => { quick.current = 0; setStuck(false); ask(true); }, fresh: () => ask(true) };
}

/** A project's chat agent: asked for each time its workspace opens (the hub returns the one that is open, reopens the
 *  last one with its conversation, or starts one inside the project). One you close stays closed while you are
 *  looking at the workspace; it comes back the next time you open it, or with the button in its place. */
function useProjectChat(project: string | null) {
  const local = useStore((s) => s.local);
  const [error, setError] = useState<string | null>(null);
  const asked = useRef<string | null>(null);
  const open = useCallback(async (name: string) => {
    try {
      await api.chatProject(name, useStore.getState().local?.hostname || null);
      setError(null);
    } catch (e) {
      setError(String((e as Error).message || e));
    }
  }, []);
  useEffect(() => {
    if (!project) { asked.current = null; return; }
    if (!local || asked.current === project) return;
    asked.current = project;
    setError(null);
    open(project);
  }, [project, local, open]);
  return { error, reopen: () => project && open(project) };
}

export default function TerminalsPage() {
  const params = useParams();
  const project = params.project || null;
  const nav = useNavigate();
  const tasks = useStore((s) => s.tasks);
  const projects = useStore((s) => s.projects);
  const boot = useStore((s) => s.bootstrapped);
  const local = useStore((s) => s.local);
  const agentRows = useStore((s) => s.agents);
  const setHomeChat = useStore((s) => s.setHomeChat);
  const loadProjects = useStore((s) => s.loadProjects);
  const now = useNow();
  const home = useHomeChat(!project);
  const pchat = useProjectChat(project);
  const [cont, setCont] = useState<Task | null>(null);
  const [stop, setStop] = useState<Task | null>(null);

  // The Home chat started a project: go where it went. The same terminal stays on screen; the lead appears beside it.
  useEffect(() => {
    if (project || !home.t || home.t.project === HOME) return;
    const dest = home.t.project;
    setHomeChat(null);
    loadProjects();
    api.materialize().catch(() => undefined);
    nav(`/p/${dest}`, { replace: true });
  }, [project, home.t, nav, setHomeChat, loadProjects]);

  const mine = useMemo(() => tasks.filter((t) => t.project === (project || HOME)), [tasks, project]);
  const chat = useMemo(() => {
    if (!project) return home.t && home.t.project === HOME && !isTerminal(home.t.state) ? home.t : null;
    return mine.find((t) => roleOf(t) === "chat" && !isTerminal(t.state)) || null;
  }, [project, home.t, mine]);
  const lead = useMemo(() => (project ? mine.find((t) => roleOf(t) === "lead" && !isTerminal(t.state)) || null : null), [project, mine]);
  const agents = useMemo(() => {
    if (!project) return [];
    const rank = (t: Task) => (loudAttention(t) ? 0 : t.kind === "session" ? 1 : 2);
    return mine.filter((t) => isLive(t) && roleOf(t) !== "chat" && roleOf(t) !== "lead").sort((a, b) => rank(a) - rank(b) || a.created_at - b.created_at);
  }, [project, mine]);
  const done = useMemo(() => (project ? mine.filter(isDoneForStrip).sort((a, b) => b.updated_at - a.updated_at).slice(0, 40) : []), [project, mine]);
  const queued = useMemo(() => (project ? mine.filter((t) => t.kind !== "session" && QUEUED.includes(t.state)).length : 0), [project, mine]);

  if (project && boot && Object.keys(projects).length && !(project in projects) && !mine.length) return <Navigate to="/" replace />;

  const chatAgent = agentRows.find((a) => a.profile === "_chat" && a.host === local?.hostname) || agentRows.find((a) => a.profile === "_chat");
  const noChatAgent = boot && !!local && !chatAgent?.alive;
  const needs = "The chat agent runs on this machine's worker and needs tmux and Claude Code here. Start the engine (hm up), or see Subscriptions on the Control tab.";

  let chatPlaceholder: React.ReactNode;
  if (!project) {
    chatPlaceholder = home.stuck
      ? <Placeholder title="The chat agent stopped right after starting" hint="Claude Code closed twice in a row. Check its sign-in on the Control tab (Subscriptions), then try again." action={<Button size="sm" variant="primary" onClick={home.retry}>Try again</Button>} />
      : home.error
        ? <Placeholder title="The chat agent could not start" hint={home.error} action={<Button size="sm" variant="secondary" onClick={home.retry}>Try again</Button>} />
        : <Placeholder title="Starting the chat agent" hint={noChatAgent ? needs : "A fresh Claude Code session is opening. Chat, or paste a brief to start a project."} />;
  } else {
    const ended = mine.some((t) => roleOf(t) === "chat" && isTerminal(t.state));
    chatPlaceholder = pchat.error
      ? <Placeholder title="Could not open the chat agent" hint={pchat.error} action={<Button size="sm" variant="secondary" onClick={pchat.reopen}>Try again</Button>} />
      : ended
        ? <Placeholder title="The chat agent is closed" hint="It reopens with its conversation." action={<Button size="sm" variant="primary" onClick={pchat.reopen} data-reopen-chat>Reopen the chat agent</Button>} />
        : <Placeholder title="Starting the chat agent" hint={noChatAgent ? needs : `The chat agent of ${project} is opening.`} />;
  }
  const hadLead = !!project && mine.some((t) => roleOf(t) === "lead");
  const startLead = () => leadApi.talk(project!).then((r) => toast(r.reopened ? "Reopening the lead with its conversation" : "Starting the lead")).catch((e) => toast.error(String(e.message)));
  const leadPlaceholder = project ? (
    <Placeholder title="No lead is running"
      hint={hadLead ? "The lead of this project has ended. It reopens with its conversation and reads the briefs waiting for it." : "Tell the chat agent what to build and it starts the lead with your brief. The lead plans, spawns the agents, and each one gets a tile here."}
      action={<Button size="sm" variant="secondary" onClick={startLead} data-start-lead>{hadLead ? "Reopen the lead" : "Start the lead"}</Button>} />
  ) : undefined;

  const paused = project ? projects[project]?.paused : false;
  const onStop = (t: Task) => {
    const role = roleOf(t);
    if (role === "chat" || (t.kind !== "session")) { act.cancel(t); return; }
    setStop(t);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {paused ? (
        <div className="flex h-8 shrink-0 items-center gap-3 border-b border-border bg-surface-2 px-3 text-[12px]" data-paused>
          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          <span className="text-fg">{project} is paused: no new task starts and nothing merges until it resumes.</span>
          <span className="text-muted">The lead resumes it once it has read the brief.</span>
          <span className="flex-1" />
          <Button size="xs" variant="secondary" onClick={() => api.resume(project!).then(() => loadProjects()).catch((e) => toast.error(String(e.message)))}>Resume now</Button>
        </div>
      ) : null}
      <div className="min-h-0 flex-1">
        <Workspace project={project} chat={chat} chatPlaceholder={chatPlaceholder} lead={lead} leadPlaceholder={leadPlaceholder}
          agents={agents} done={done} queued={queued} now={now} onStop={onStop} onContinue={setCont} onFreshChat={home.fresh} />
      </div>
      <ContinueDialog task={cont} open={!!cont} onOpenChange={(o) => !o && setCont(null)} />
      <ConfirmDialog open={!!stop} onOpenChange={(o) => !o && setStop(null)}
        title={stop ? (roleOf(stop) === "lead" ? `Stop the lead of ${stop.project}?` : `Stop agent ${stop.id.slice(0, 8)}?`) : ""}
        body={stop && roleOf(stop) === "lead" ? "Its terminal closes. Agents it already started keep working; the lead reopens with its conversation." : "This closes its terminal. Uncommitted work in its worktree is kept on disk."}
        confirmLabel="Stop" danger onConfirm={() => { if (stop) act.cancel(stop); }} />
    </div>
  );
}
