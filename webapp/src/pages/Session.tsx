import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Group, Panel, Separator } from "react-resizable-panels";
import { ArrowLeft } from "lucide-react";
import { useStore, useTask } from "@/lib/store";
import { api } from "@/lib/api";
import { agentLabel, cn, elapsed, firstLine, flagAttention, isTerminal, sessionOf } from "@/lib/utils";
import { AgentChip, EmptyState, SectionTitle, StepTimer } from "@/components/bits";
import { StateBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Tip } from "@/components/ui/fields";
import { AttentionPanel } from "@/components/Attention";
import { EventStream } from "@/components/EventStream";
import { Terminal } from "@/components/Terminal";
import { ConfirmDialog, ContinueDialog, RetryDialog } from "@/components/dialogs";
import { StandingOrders } from "@/components/StandingOrders";
import { DiffView } from "./Task";
import { act } from "@/components/actions";

export default function SessionPage() {
  const { id = "" } = useParams();
  const t = useTask(id);
  const byTask = useStore((s) => s.byTask);
  const local = useStore((s) => s.local);
  const theme = useStore((s) => s.theme);
  const [text, setText] = useState("");
  const [cont, setCont] = useState(false);
  const [retry, setRetry] = useState(false);
  const [confirm, setConfirm] = useState<null | "finish" | "cancel" | "delete" | "merge">(null);
  const [now, setNow] = useState(Date.now() / 1000);
  const [detailErr, setDetailErr] = useState<string>("");
  const inputRef = useRef<HTMLInputElement>(null);
  const sess = useMemo(() => sessionOf(t), [t]);
  const ended = t ? isTerminal(t.state) : false;
  const att = t && !ended ? sess.attention || null : null;
  const flag = t && !ended ? flagAttention(t) : null;
  const entries = byTask[id] || [];
  const [orders, setOrders] = useState(false);
  const hasTerm = !!t && !ended && !!sess.tmux && !!sess.host && sess.host === local?.hostname;

  useEffect(() => {
    const i = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(i);
  }, []);
  useEffect(() => {
    if (id) api.sessionViewed(id).catch(() => undefined);
  }, [id, sess.unread]);
  useEffect(() => {
    if (!id || !ended) return;
    api.task(id).then((d) => {
      const last = [...d.attempts].reverse().find((a) => a.verifier_log);
      setDetailErr(last?.verifier_log || "");
    }).catch(() => undefined);
  }, [id, ended]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.closest(".xterm"));
      if (typing) {
        if (e.key === "Escape" && !target.closest(".xterm")) (target as HTMLElement).blur();
        return;
      }
      if (!t || ended) return;
      if (e.key === "t") { e.preventDefault(); inputRef.current?.focus(); return; }
      if (e.key === "y" && att?.kind === "permission") { e.preventDefault(); act.approve(id); return; }
      if (e.key === "d" && att?.kind === "permission") { e.preventDefault(); act.deny(id); return; }
      if (/^[1-9]$/.test(e.key) && att?.kind === "question") {
        const n = Number(e.key);
        const opts = att.options || [];
        if (n <= opts.length) { e.preventDefault(); act.option(id, n, opts[n - 1]); }
        return;
      }
      if (e.key === "F") { e.preventDefault(); setConfirm("finish"); return; }
      if (e.key === "o") { e.preventDefault(); setCont(true); return; }
      if (e.key === "c") { e.preventDefault(); setConfirm("cancel"); return; }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [id, t, ended, att]);

  if (!t) {
    return <EmptyState title="Session not found" hint="It may have been deleted, or the daemon hasn't reported it yet." action={<Link to="/"><Button>Back to the swarm</Button></Link>} />;
  }
  const agent = sess.agent || t.claimed_by;
  const send = async () => {
    const v = text.trim();
    if (!v) return;
    if (att?.kind === "question") await act.answerText(id, v);
    else if (att?.kind === "permission") await act.deny(id, v);
    else await act.send(id, v);
    setText("");
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-11 shrink-0 flex-wrap items-center gap-2 border-b border-border bg-surface px-4 py-1.5">
        <Link to="/" className="rounded-md p-1 text-muted hover:bg-surface-2 hover:text-fg"><ArrowLeft className="h-4 w-4" /></Link>
        <AgentChip agent={agent} lead={!!sess.lead} session />
        <span className="mono text-muted">{t.id.slice(0, 8)}</span>
        {sess.name ? <span className="mono text-dim">{sess.name}</span> : null}
        <StateBadge state={t.state} live attention={!!att} />
        {!ended && sess.turn && !att ? <span className="text-meta">{sess.turn}</span> : null}
        <span className="text-meta">{sess.permission_mode || "auto"}</span>
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium" title={t.spec}>{firstLine(t.spec, 120)}</span>
        {!ended ? <StepTimer entries={entries} agent={agent} now={now} className="max-w-[320px]" /> : null}
        <span className="num hidden text-meta lg:inline">{elapsed(sess.started_at || t.updated_at, now)} · {sess.turns || 0} turns · {sess.asks || 0} asks · {sess.auto_approved || 0} auto-ok</span>
        <div className="flex items-center gap-1">
          {!ended ? (
            <>
              <Tip label="Commit, push, verify, close the terminal"><Button size="sm" variant="primary" onClick={() => setConfirm("finish")} kbd="F">Finish</Button></Tip>
              <Tip label="Hand over to another agent with its work and context"><Button size="sm" variant="secondary" onClick={() => setCont(true)} kbd="o">Continue on…</Button></Tip>
              <Tip label="Kill the agent"><Button size="sm" variant="ghost" className="hover:text-danger" onClick={() => setConfirm("cancel")} kbd="c">Cancel</Button></Tip>
            </>
          ) : (
            <>
              {t.state === "done" ? <Button size="sm" variant="primary" onClick={() => setConfirm("merge")}>Merge</Button> : null}
              <Button size="sm" variant="secondary" onClick={() => setRetry(true)}>Reopen</Button>
              <Button size="sm" variant="ghost" className="hover:text-danger" onClick={() => setConfirm("delete")}>Delete</Button>
            </>
          )}
        </div>
      </div>

      {att || flag ? (
        <div className="grid shrink-0 gap-2 border-b border-border bg-surface px-4 py-3">
          {flag ? <AttentionPanel tid={id} att={flag} onCancel={() => setConfirm("cancel")} onContinue={() => setCont(true)} /> : null}
          {att ? <AttentionPanel tid={id} att={att} onContinue={() => setCont(true)} onFinish={() => setConfirm("finish")} /> : null}
        </div>
      ) : null}

      <div className="min-h-0 flex-1">
        <Group orientation="horizontal" id="hm-session-split" className="h-full">
          <Panel defaultSize="62" minSize="30" className="min-h-0">
            <div className="flex h-full min-h-0 flex-col">
              {ended ? (
                <div className="flex h-full min-h-0 flex-col overflow-y-auto p-4">
                  <SectionTitle>Result</SectionTitle>
                  <div className="card mb-4 p-4 text-[13px]">
                    {t.state === "done" ? <span><span className="text-success">Finished and verified</span> · branch <span className="mono">hiveswarm/{t.id}</span></span> : null}
                    {t.state === "failed" ? <span className="text-danger">Failed{detailErr ? ":" : ""}</span> : null}
                    {t.state === "abandoned" ? <span className="text-muted">Cancelled{sess.continued_in ? <>, continued in <Link className="mono underline" to={`/sessions/${sess.continued_in}`}>{sess.continued_in.slice(0, 8)}</Link></> : ""}</span> : null}
                    {detailErr && t.state === "failed" ? <pre className="mono mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface-2 p-2 text-muted">{detailErr.slice(-3000)}</pre> : null}
                  </div>
                  {t.state === "done" ? <DiffView id={id} /> : null}
                </div>
              ) : hasTerm ? (
                <Terminal tid={id} dark={theme === "dark"} className="h-full" />
              ) : (
                <div className="flex h-full items-center justify-center p-6">
                  <EmptyState title={sess.host ? `The terminal is on ${sess.host}` : "Not started yet"} hint={sess.host ? "Open the app on that machine to see and type into it. The event stream on the right works from anywhere." : `Waiting for a free ${agentLabel(agent)} lane on a worker.`} />
                </div>
              )}
              {!ended ? (
                <form className="flex shrink-0 items-center gap-2 border-t border-border bg-surface px-3 py-2" onSubmit={(e) => { e.preventDefault(); send(); }}>
                  <Input ref={inputRef} value={text} onChange={(e) => setText(e.target.value)}
                    placeholder={att?.kind === "question" ? "type your own answer" : att?.kind === "permission" ? "deny with a message: what to do instead" : "message for this agent · t to focus · enter sends · esc back"}
                    className="h-8" />
                  <Button type="submit" size="md" variant="secondary" disabled={!text.trim()}>Send</Button>
                </form>
              ) : null}
            </div>
          </Panel>
          <Separator className="w-px bg-border transition-colors hover:bg-border-strong data-[state=active]:bg-accent" />
          <Panel defaultSize="38" minSize="20" className="min-h-0">
            <div className="flex h-full min-h-0 flex-col bg-surface">
              <div className="flex h-9 shrink-0 items-center gap-3 border-b border-border px-3 text-[12px]">
                <button type="button" className={cn("text-label rounded-sm", !orders ? "text-fg" : "hover:text-fg")} onClick={() => setOrders(false)}>Events</button>
                <button type="button" className={cn("text-label rounded-sm", orders ? "text-fg" : "hover:text-fg")} onClick={() => setOrders(true)}>Standing orders</button>
                <span className="flex-1" />
                <span className="num text-meta">{entries.length}</span>
              </div>
              <div className="min-h-0 flex-1">
                {orders ? (
                  <div className="h-full overflow-y-auto p-3">
                    <StandingOrders taskId={id} project={t.project} />
                    <div className="mt-3 text-[12px] leading-[18px] text-dim">Orders sit at the top of the agent's prompt and are re-injected on the cadence shown: Claude Code gets them mid-turn through hooks; other agents get them typed in between turns. With auditing on, a likely violation appears above and in the inbox.</div>
                  </div>
                ) : (
                  <EventStream entries={entries} emptyText="waiting for the first event" />
                )}
              </div>
            </div>
          </Panel>
        </Group>
      </div>

      <ContinueDialog task={t} open={cont} onOpenChange={setCont} />
      <RetryDialog task={t} lastError={detailErr} open={retry} onOpenChange={setRetry} />
      <ConfirmDialog open={confirm === "finish"} onOpenChange={(o) => !o && setConfirm(null)} title={`Finish session ${t.id.slice(0, 8)}?`} body="Its worktree is committed, pushed as a branch, and verified. The agent's terminal closes." confirmLabel="Finish" onConfirm={() => act.finish(id)} />
      <ConfirmDialog open={confirm === "cancel"} onOpenChange={(o) => !o && setConfirm(null)} title={`Cancel ${t.id.slice(0, 8)}?`} body="This kills the running agent. Uncommitted work in its worktree is kept on disk." confirmLabel="Cancel session" danger onConfirm={() => act.cancel(t)} />
      <ConfirmDialog open={confirm === "delete"} onOpenChange={(o) => !o && setConfirm(null)} title={`Delete ${t.id.slice(0, 8)}?`} body="Removes the record and its worktree. This cannot be undone." confirmLabel="Delete" danger onConfirm={() => act.remove(id)} />
      <ConfirmDialog open={confirm === "merge"} onOpenChange={(o) => !o && setConfirm(null)} title={`Merge hiveswarm/${t.id.slice(0, 8)}?`} body="Into the project's current branch on the hub." confirmLabel="Merge" onConfirm={() => act.merge(id)} />
    </div>
  );
}

export function cnx(...a: string[]) {
  return cn(...a);
}
