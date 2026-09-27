import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AnimatePresence, motion } from "motion/react";
import { toast } from "sonner";
import { ChevronDown, ChevronRight, Crown, FolderOpen, FolderPlus, Loader2, RefreshCw, RotateCcw, SendHorizontal, Sparkles, TerminalSquare, Trash2, Waypoints } from "lucide-react";
import { api, leadApi } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { FeedEntry, Task } from "@/lib/types";
import { age, agentColor, agentLabel, classifyEntry, clock, cn, firstLine, isLive, isTerminal, liveAttention, sessionOf, short, taskAgent } from "@/lib/utils";
import { AgentChip, EmptyState, TaskKindIcon, taskHref } from "@/components/bits";
import { Badge, StateBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea, Tip } from "@/components/ui/fields";
import { AttentionPanel } from "@/components/Attention";
import { Terminal } from "@/components/Terminal";
import { StandingOrders } from "@/components/StandingOrders";
import { DeleteProjectDialog } from "@/components/dialogs";
import { act } from "@/components/actions";

type Msg =
  | { kind: "you"; ts: number; text: string; id: number }
  | { kind: "lead"; ts: number; text: string; id: number }
  | { kind: "tools"; ts: number; id: number; items: { tool: string; result?: string; ts: number }[] }
  | { kind: "system"; ts: number; text: string; id: number; taskId?: string; tone?: "note" | "ask" | "good" | "bad" | "warn" };

function toMessages(entries: FeedEntry[]): Msg[] {
  const out: Msg[] = [];
  let pendingTools: { tool: string; result?: string; ts: number }[] = [];
  let toolsStartId = 0, toolsStartTs = 0;
  const flushTools = () => {
    if (pendingTools.length) out.push({ kind: "tools", ts: toolsStartTs, id: toolsStartId, items: pendingTools });
    pendingTools = [];
  };
  for (const e of entries) {
    const { kind } = classifyEntry(e.source, e.chunk);
    const c = e.chunk.trim();
    if (kind === "you") {
      flushTools();
      if (e.source === "daemon") {
        const m = c.match(/^you(?: \(queued until the lead starts\))?: (.*)$/s);
        if (m) out.push({ kind: "you", ts: e.ts, text: m[1], id: e.id });
        continue;
      }
      if (c.startsWith("You are the standing Hiveswarm lead") || c.startsWith("You are the Hiveswarm lead")) {
        const m = c.match(/\n\nRequest:\n([\s\S]*?)\n\nHow to work:/) || c.match(/\n\nGoal:\n([\s\S]*?)\n\nHow to work:/);
        out.push({ kind: "system", ts: e.ts, text: "lead started", id: e.id, tone: "note" });
        const prevYou = [...out].reverse().find((x) => x.kind === "you");
        if (m && m[1].trim() && !m[1].startsWith("Standing by") && !(prevYou && prevYou.kind === "you" && prevYou.text === m[1].trim())) {
          out.push({ kind: "you", ts: e.ts, text: m[1].trim(), id: e.id + 0.5 });
        }
        continue;
      }
      const ans = c.match(/^\[AskUserQuestion answered\] (.*)$/s);
      if (ans) continue;
      const prevYou = [...out].reverse().find((m) => m.kind === "you");
      if (prevYou && prevYou.kind === "you" && (prevYou.text === c || c.startsWith(prevYou.text.slice(0, 200)))) continue;
      out.push({ kind: "you", ts: e.ts, text: c, id: e.id });
      continue;
    }
    if (kind === "msg") {
      flushTools();
      out.push({ kind: "lead", ts: e.ts, text: c, id: e.id });
      continue;
    }
    if (kind === "tool") {
      if (!pendingTools.length) { toolsStartId = e.id; toolsStartTs = e.ts; }
      pendingTools.push({ tool: c.replace(/^mcp__hiveswarm__/, ""), ts: e.ts });
      continue;
    }
    if (kind === "result") {
      if (pendingTools.length) pendingTools[pendingTools.length - 1].result = c;
      continue;
    }
    if (e.source === "daemon") {
      if (/^you chose|^you answered|^you denied|^you approved/.test(c)) {
        flushTools();
        out.push({ kind: "you", ts: e.ts, text: c.replace(/^you (chose|answered|denied|approved): /, ""), id: e.id });
        continue;
      }
      const dispatched = c.match(/^enqueued in \S+ by lead \S+(?: \(prefer (\S+)\))?: (.*)$/s);
      if (kind === "ask") {
        flushTools();
        out.push({ kind: "system", ts: e.ts, text: c.replace(/^question for you: /, "asked: "), id: e.id, tone: "ask" });
        continue;
      }
      if (kind === "good" || kind === "bad" || kind === "warn") {
        flushTools();
        out.push({ kind: "system", ts: e.ts, text: c, id: e.id, tone: kind });
        continue;
      }
      if (dispatched) continue;
      continue;
    }
    if (kind === "status" && /^(lead session finished|exited|worker restarted)/.test(c)) {
      flushTools();
      out.push({ kind: "system", ts: e.ts, text: c, id: e.id, tone: c.startsWith("exited") ? "bad" : "note" });
    }
  }
  flushTools();
  return out;
}

function Bubble({ m }: { m: Msg }) {
  const [open, setOpen] = useState(false);
  if (m.kind === "you") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[78%] rounded-2xl rounded-br-md bg-accent/15 px-3.5 py-2 text-[13.5px] leading-relaxed text-fg ring-1 ring-accent/25 whitespace-pre-wrap">{m.text}</div>
      </div>
    );
  }
  if (m.kind === "lead") {
    return (
      <div className="flex items-start gap-2.5">
        <span className="mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface-3 text-accent"><Crown className="h-3.5 w-3.5" /></span>
        <div className="max-w-[82%] rounded-2xl rounded-tl-md bg-surface-2 px-3.5 py-2 text-[13.5px] leading-relaxed text-fg ring-1 ring-border whitespace-pre-wrap">{m.text}</div>
      </div>
    );
  }
  if (m.kind === "tools") {
    return (
      <div className="pl-[34px]">
        <button onClick={() => setOpen((v) => !v)} className="flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[11.5px] text-dim hover:bg-surface-2 hover:text-muted">
          {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          {m.items.length} tool call{m.items.length === 1 ? "" : "s"}: <span className="mono">{m.items.map((i) => i.tool.split(":")[0]).slice(0, 4).join(" · ")}{m.items.length > 4 ? " …" : ""}</span>
        </button>
        {open ? (
          <div className="mt-1 grid gap-1 rounded-md border border-border bg-bg/40 p-2">
            {m.items.map((it, i) => (
              <div key={i} className="mono text-[11.5px]">
                <span className="text-info">▸ {it.tool}</span>
                {it.result ? <div className="mt-0.5 max-h-24 overflow-auto whitespace-pre-wrap text-muted">{it.result.slice(0, 600)}</div> : null}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    );
  }
  const tone = m.tone === "good" ? "text-success" : m.tone === "bad" ? "text-danger" : m.tone === "warn" || m.tone === "ask" ? "text-warn" : "text-dim";
  return (
    <div className="flex justify-center">
      <span className={cn("rounded-full border border-border bg-surface px-2.5 py-0.5 text-[11px]", tone)}>{m.taskId ? <Link to={`/t/${m.taskId}`} className="hover:underline">{m.text}</Link> : m.text}</span>
    </div>
  );
}

export default function LeadPage() {
  const { project: projectParam } = useParams();
  const nav = useNavigate();
  const projects = useStore((s) => s.projects);
  const loadProjects = useStore((s) => s.loadProjects);
  const tasks = useStore((s) => s.tasks);
  const taskById = useStore((s) => s.taskById);
  const byTask = useStore((s) => s.byTask);
  const feed = useStore((s) => s.feed);
  const local = useStore((s) => s.local);
  const theme = useStore((s) => s.theme);
  const names = Object.keys(projects);
  const project = projectParam && projects[projectParam] ? projectParam : names[0];
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [showTerm, setShowTerm] = useState(false);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [del, setDel] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    loadProjects();
    const i = setInterval(loadProjects, 20000);
    return () => clearInterval(i);
  }, []);
  const proj = project ? projects[project] : undefined;
  const copy = proj?.local || null;
  const openFolder = async () => {
    if (!project) return;
    try {
      const r = await api.openLocal(project);
      if (!r.ok) toast.warning(r.reason || "could not open the folder");
    } catch (e) {
      toast.error(String((e as Error).message));
    }
  };
  const syncNow = async () => {
    if (!project) return;
    setSyncing(true);
    try {
      const r = await api.syncLocal(project);
      if (r.ok) toast.success(r.note || "up to date");
      else toast.warning(r.reason || "could not sync");
      await loadProjects();
    } catch (e) {
      toast.error(String((e as Error).message));
    } finally {
      setSyncing(false);
    }
  };
  useEffect(() => { if (!projectParam && names[0]) nav(`/lead/${names[0]}`, { replace: true }); }, [projectParam, names.length]);

  const lead = useMemo(() => {
    const leads = tasks.filter((t) => t.kind === "session" && t.project === project && sessionOf(t).lead && sessionOf(t).persistent);
    const live = leads.find((t) => !isTerminal(t.state));
    return live || leads.sort((a, b) => b.created_at - a.created_at)[0] || null;
  }, [tasks, project]);
  const sess = lead ? sessionOf(lead) : {};
  const att = lead ? liveAttention(lead) : null;
  const entries = lead ? byTask[lead.id] || [] : [];
  const messages = useMemo(() => toMessages(entries), [entries]);
  const dispatched = useMemo(() => {
    if (!lead) return [] as Task[];
    const tag = `by lead ${lead.id.slice(0, 8)}`;
    const ids = new Set(feed.filter((e) => e.source === "daemon" && e.chunk.includes(tag)).map((e) => e.task_id));
    return Array.from(ids).map((id) => taskById[id]).filter(Boolean).sort((a, b) => b.updated_at - a.updated_at);
  }, [feed, lead?.id, taskById]);
  const hasTerm = !!lead && !isTerminal(lead.state) && !!sess.tmux && sess.host === local?.hostname;
  const lastId = messages.length ? messages[messages.length - 1].id : 0;
  useLayoutEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }); }, [lastId, messages.length, att?.kind, showTerm]);

  const send = async () => {
    const v = text.trim();
    if (!v || !project) return;
    setSending(true);
    try {
      if (lead && !isTerminal(lead.state) && att && (att.kind === "question" || att.kind === "permission")) {
        if (att.kind === "question") await act.answerText(lead.id, v);
        else await act.deny(lead.id, v);
      } else {
        const r = await leadApi.talk(project, v);
        if (r.created) toast(`Starting the lead for ${project} — it reads the repo, then answers here`, { duration: 6000 });
        else if (r.reopened) toast(`Reopening the lead for ${project} with its memory`, { duration: 6000 });
      }
      setText("");
    } catch (e) {
      toast.error(String((e as Error).message));
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  };

  const createProject = async () => {
    const n = newName.trim();
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(n)) { toast.warning("letters, digits, - and _ only"); return; }
    setCreating(true);
    try {
      const r = await api.createProject(n);
      toast.success(`Created ${r.name} at ${r.repo_path}`);
      await loadProjects();
      setNewName("");
      nav(`/lead/${r.name}`);
    } catch (e) {
      toast.error(String((e as Error).message));
    } finally {
      setCreating(false);
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.closest(".xterm"));
      if (typing) return;
      if (e.key === "t" || e.key === "Enter") { e.preventDefault(); inputRef.current?.focus(); }
      if (lead && att?.kind === "question" && /^[1-9]$/.test(e.key)) {
        const n = Number(e.key); const opts = att.options || [];
        if (n <= opts.length) { e.preventDefault(); act.option(lead.id, n, opts[n - 1]); }
      }
      if (lead && att?.kind === "permission" && e.key === "y") { e.preventDefault(); act.approve(lead.id); }
      if (lead && att?.kind === "permission" && e.key === "d") { e.preventDefault(); act.deny(lead.id); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lead?.id, att]);

  const turnTone = sess.turn === "working" ? "info" : sess.turn === "waiting" ? "warn" : sess.turn === "idle" ? "success" : "dim";
  const leadEnded = !!lead && isTerminal(lead.state);
  const leadQueued = !!lead && !leadEnded && !sess.host;

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-[232px] shrink-0 flex-col border-r border-border bg-surface/60">
        <div className="px-3 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wider text-dim">Projects</div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2">
          {names.map((n) => {
            const l = tasks.find((t) => t.kind === "session" && t.project === n && sessionOf(t).lead && sessionOf(t).persistent && !isTerminal(t.state));
            const la = l ? liveAttention(l) : null;
            const live = tasks.filter((t) => t.project === n && isLive(t) && t.id !== l?.id).length;
            return (
              <div key={n} className={cn("group mb-0.5 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px]", n === project ? "bg-surface-2 text-fg" : "text-muted hover:bg-surface-2/60 hover:text-fg")}>
                <button onClick={() => nav(`/lead/${n}`)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                  <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", la ? "bg-warn live-dot" : l ? "bg-success" : "bg-dim")} />
                  <span className="min-w-0 flex-1 truncate">{n}</span>
                  {live ? <span className="num text-[10.5px] text-dim">{live}</span> : null}
                </button>
                <Tip label="Remove this project from Hiveswarm"><button onClick={() => setDel(n)} className="-mr-1 rounded p-0.5 text-dim opacity-0 hover:text-danger group-hover:opacity-100"><Trash2 className="h-3.5 w-3.5" /></button></Tip>
              </div>
            );
          })}
          {!names.length ? <div className="px-2 py-3 text-[12px] text-dim">No projects yet — create one below.</div> : null}
        </div>
        <div className="border-t border-border p-2">
          <div className="flex items-center gap-1">
            <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="new project…" className="h-7 text-[12px]" onKeyDown={(e) => { if (e.key === "Enter") createProject(); }} />
            <Tip label="Create an empty repo on the hub"><Button size="icon-sm" variant="ghost" onClick={createProject} disabled={creating || !newName.trim()}>{creating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FolderPlus className="h-3.5 w-3.5" />}</Button></Tip>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center gap-2 border-b border-border bg-surface px-4 py-2">
          <Crown className="h-4 w-4 text-accent" />
          <span className="text-[13.5px] font-semibold">Lead</span>
          <span className="text-[12.5px] text-muted">for <b className="text-fg">{project || "—"}</b></span>
          {lead ? (
            <>
              <Link to={`/sessions/${lead.id}`} className="mono text-[11.5px] text-dim hover:text-fg">{short(lead.id)}</Link>
              {leadEnded ? <StateBadge state={lead.state} /> : leadQueued ? <Badge tone="dim">waiting for a lane</Badge> : <Badge tone={turnTone as never} dot={sess.turn === "working"}>{sess.turn}</Badge>}
              <span className="num text-[11px] text-dim">turns {sess.turns || 0}</span>
            </>
          ) : <Badge tone="dim">not started</Badge>}
          <span className="flex-1" />
          {lead && !leadEnded ? (
            <>
              <Button size="sm" variant={showTerm ? "secondary" : "ghost"} onClick={() => setShowTerm((v) => !v)} disabled={!hasTerm}><TerminalSquare className="h-3.5 w-3.5" /> terminal</Button>
              <Tip label="Kill the lead (its memory survives; the next message reopens it)"><Button size="sm" variant="ghost" className="text-danger" onClick={() => act.cancel(lead)}>stop</Button></Tip>
            </>
          ) : null}
          {lead && leadEnded ? <Button size="sm" variant="secondary" onClick={() => leadApi.talk(project!).then(() => toast("Reopening the lead")).catch((e) => toast.error(String(e.message)))}><RotateCcw className="h-3.5 w-3.5" /> reopen</Button> : null}
        </div>

        {project ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border bg-surface/60 px-4 py-1.5 text-[12px]">
            <FolderOpen className="h-3.5 w-3.5 text-muted" />
            {copy ? (
              <>
                <span className="text-muted">{copy.host === local?.hostname ? "files on this machine:" : `files on ${copy.host}:`}</span>
                <button onClick={openFolder} className="mono text-fg hover:underline underline-offset-2" title={copy.win_path || copy.path}>{copy.path.replace(/^\/home\/[^/]+/, "~")}</button>
                {copy.note ? <span className={cn("text-[11.5px]", copy.dirty || copy.ahead ? "text-warn" : "text-dim")}>· {copy.note}</span> : null}
                {copy.synced_at ? <span className="text-[11px] text-dim">· checked {age(copy.synced_at)} ago</span> : null}
              </>
            ) : (
              <span className="text-dim">no local copy yet — the worker makes one within a minute (local_projects in worker.toml)</span>
            )}
            <span className="flex-1" />
            {copy ? <Tip label="Reveal the folder in Explorer"><Button size="xs" variant="ghost" onClick={openFolder}><FolderOpen className="h-3 w-3" /> open folder</Button></Tip> : null}
            <Tip label="Fast-forward this machine's copy from the hub now"><Button size="xs" variant="ghost" onClick={syncNow} disabled={syncing}><RefreshCw className={cn("h-3 w-3", syncing && "animate-spin")} /> sync now</Button></Tip>
            <span className="mono text-[11px] text-dim" title="the canonical repository, on the hub">hub: {proj?.repo_path}</span>
          </div>
        ) : null}

        <div className="flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 flex-col">
            {showTerm && hasTerm && lead ? (
              <div className="min-h-0 flex-1"><Terminal tid={lead.id} dark={theme === "dark"} className="h-full" /></div>
            ) : (
              <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
                <div className="mx-auto flex max-w-3xl flex-col gap-3 px-5 py-5">
                  {!lead ? (
                    <EmptyState icon={<Sparkles className="h-8 w-8" />} title={project ? `Tell the lead what ${project} needs` : "Pick or create a project"} hint="It reads the repo, plans, asks the decide model and the routing data how big the swarm should be and who should do what, dispatches, watches, reviews and merges — and tells you here. Even a one-line fix goes through the swarm so it's verified." />
                  ) : null}
                  {lead && messages.length === 0 ? <div className="text-center text-[12.5px] text-dim">{leadQueued ? "the lead is queued behind other work; your message is kept for it" : "starting…"}</div> : null}
                  <AnimatePresence initial={false}>
                    {messages.map((m) => (
                      <motion.div key={m.id} layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.18 }}>
                        <Bubble m={m} />
                      </motion.div>
                    ))}
                  </AnimatePresence>
                  {lead && !leadEnded && sess.turn === "working" ? (
                    <div className="flex items-center gap-2 pl-[34px] text-[12px] text-dim"><Loader2 className="h-3 w-3 animate-spin" /> working…</div>
                  ) : null}
                  {lead && att && att.kind !== "input" ? <AttentionPanel tid={lead.id} att={att} /> : null}
                </div>
              </div>
            )}
            <div className="shrink-0 border-t border-border bg-surface px-4 py-3">
              <div className="mx-auto flex max-w-3xl items-end gap-2">
                <Textarea
                  ref={inputRef}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  rows={Math.min(6, Math.max(1, text.split("\n").length))}
                  placeholder={!project ? "create a project first" : att?.kind === "question" ? "or type your own answer…" : att?.kind === "permission" ? "deny with a message…" : lead ? "Tell the lead what to do next…" : "What should the swarm build? Describe the goal; the lead sizes and runs it."}
                  className="min-h-[40px] resize-none py-2.5"
                  disabled={!project}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                />
                <Button variant="accent" size="lg" onClick={send} disabled={sending || !text.trim() || !project}>
                  {sending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <SendHorizontal className="h-3.5 w-3.5" />} Send
                </Button>
              </div>
              <div className="mx-auto mt-1 max-w-3xl text-[11px] text-dim">enter sends · shift+enter new line · it plans with the decide model and dispatches the swarm; watch it on the right</div>
            </div>
          </div>

          <aside className="hidden w-[300px] shrink-0 flex-col border-l border-border bg-surface/40 lg:flex">
            <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-dim"><Waypoints className="h-3.5 w-3.5" /> This lead's swarm <span className="num ml-auto normal-case tracking-normal text-dim">{dispatched.length}</span></div>
            <div className="min-h-0 flex-1 overflow-y-auto p-2">
              {dispatched.length ? dispatched.map((t) => {
                const a = liveAttention(t);
                return (
                  <Link key={t.id} to={taskHref(t)} className={cn("mb-1.5 block rounded-md border border-border bg-surface p-2 hover:border-border-strong", a && "attention-glow")}>
                    <div className="flex items-center gap-1.5">
                      <TaskKindIcon t={t} />
                      <span className="mono text-[11px] text-muted">{short(t.id)}</span>
                      <StateBadge state={t.state} />
                      <span className="ml-auto text-[10.5px] font-medium" style={{ color: agentColor(taskAgent(t)) }}>{taskAgent(t) ? agentLabel(taskAgent(t)) : "unrouted"}</span>
                    </div>
                    <div className="mt-1 line-clamp-2 text-[12px] leading-snug text-fg">{firstLine(t.spec, 120)}</div>
                    {a ? <div className="mt-1 text-[11px] text-warn">{a.kind === "permission" ? "needs approval" : a.kind === "question" ? "asks a question" : a.kind === "directive" ? "may be breaking a standing order" : a.kind}</div> : null}
                  </Link>
                );
              }) : <div className="px-2 py-6 text-center text-[12px] text-dim">tasks and sessions the lead dispatches appear here</div>}
            </div>
            {project ? (
              <div className="max-h-[42%] shrink-0 overflow-y-auto border-t border-border p-2">
                <StandingOrders project={project} title="Project standing orders" />
              </div>
            ) : null}
          </aside>
        </div>
      </div>
      <DeleteProjectDialog name={del} repoPath={del ? projects[del]?.repo_path : undefined} localPath={del ? projects[del]?.local?.path : null} open={!!del} onOpenChange={(o) => !o && setDel(null)} onDeleted={async () => { await loadProjects(); if (del === project) nav("/lead"); }} />
    </div>
  );
}

export function agentChipFor(t: Task) {
  return <AgentChip agent={taskAgent(t)} size="sm" />;
}
