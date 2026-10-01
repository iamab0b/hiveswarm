import { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { Attention } from "@/lib/types";
import { attentionLabel, cn } from "@/lib/utils";
import { Button } from "./ui/button";
import { Input } from "./ui/fields";
import { act } from "./actions";

/**
 * The one loud element of the app: a neutral panel with a 2 px accent rule (red for a high-risk approval),
 * a label saying what the agent needs, the text, then the actions. Everything else on the page stays quiet.
 */
export function AttentionPanel({ tid, att, compact, isSession = true, onContinue, onFinish, onCancel, className }: {
  tid: string;
  att: Attention;
  compact?: boolean;
  isSession?: boolean;
  onContinue?: () => void;
  onFinish?: () => void;
  onCancel?: () => void;
  className?: string;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [showDetail, setShowDetail] = useState(false);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
      setText("");
    } finally {
      setBusy(false);
    }
  };
  const options = att.options || [];
  const severe = att.kind === "failed" || (att.kind === "permission" && att.risk === "high");
  const inputCls = compact ? "h-7 text-[12px]" : "h-8";
  return (
    <div className={cn("rounded-md border border-border bg-surface-2 py-2.5 pl-3 pr-3", severe ? "border-l-2 border-l-danger" : "border-l-2 border-l-accent", className)} data-attention={att.kind}>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className={cn("text-label", severe ? "text-danger" : "text-accent")}>{attentionLabel(att)}</span>
        {att.risk ? <span className="text-meta">{att.risk} risk</span> : null}
      </div>
      <div className={cn("mt-0.5 break-words text-fg", compact ? "text-[12.5px] leading-[18px]" : "text-[13px] leading-5")}>{att.summary}</div>
      {att.kind === "permission" && att.detail && att.detail !== att.summary.split(": ").slice(1).join(": ") ? (
        <pre className="mono mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface p-2 text-muted">{att.detail}</pre>
      ) : null}

      {att.kind === "permission" ? (
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <Button variant="primary" size="sm" disabled={busy} onClick={() => run(() => act.approve(tid))} kbd="y">Approve</Button>
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => run(() => act.deny(tid))} kbd="d">Deny</Button>
          <form className="flex min-w-[220px] flex-1 items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); if (text.trim()) run(() => act.deny(tid, text)); }}>
            <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="deny with a message: what to do instead" className={inputCls} />
            <Button size="sm" variant="secondary" type="submit" disabled={busy || !text.trim()}>Send</Button>
          </form>
        </div>
      ) : null}

      {att.kind === "question" ? (
        <div className="mt-2.5 flex flex-col gap-2">
          {options.length ? (
            <div className="flex flex-wrap gap-1.5">
              {options.map((o, i) => (
                <Button key={i} size="sm" variant="secondary" disabled={busy} onClick={() => run(() => act.option(tid, i + 1, o))} className="max-w-full">
                  <kbd className="key">{i + 1}</kbd>
                  <span className="truncate">{o}</span>
                </Button>
              ))}
            </div>
          ) : null}
          <form className="flex items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); if (text.trim()) run(() => act.answerText(tid, text)); }}>
            <Input value={text} onChange={(e) => setText(e.target.value)} placeholder={options.length ? "or type your own answer" : "type your answer"} className={inputCls} />
            <Button size="sm" variant="secondary" type="submit" disabled={busy || !text.trim()}>Answer</Button>
          </form>
        </div>
      ) : null}

      {att.kind === "input" ? (
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <form className="flex min-w-[240px] flex-1 items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); if (text.trim()) run(() => act.send(tid, text)); }}>
            <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="next instruction" className={inputCls} />
            <Button size="sm" variant="secondary" type="submit" disabled={busy || !text.trim()}>Send</Button>
          </form>
          {onFinish ? <Button size="sm" variant="primary" onClick={onFinish} kbd="F">Finish</Button> : null}
        </div>
      ) : null}

      {att.kind === "usage_limit" ? (
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          {onContinue ? <Button size="sm" variant="primary" onClick={onContinue} kbd="o">Continue on another agent</Button> : null}
          <span className="text-[12px] text-muted">its work is saved and handed over; or wait and reopen it later</span>
        </div>
      ) : null}

      {att.kind === "stalled" ? (
        <div className="mt-2 flex flex-col gap-2">
          <div className="text-[12px] text-muted">{att.detail || "One step has taken much longer than usual. Either it is a legitimately long command or the agent is stuck."}</div>
          <div className="flex flex-wrap items-center gap-2">
            {onCancel ? <Button size="sm" variant="destructive" disabled={busy} onClick={onCancel}>Stop it</Button> : null}
            {isSession && onContinue ? <Button size="sm" variant="secondary" disabled={busy} onClick={onContinue} kbd="o">Continue on another agent</Button> : null}
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => act.clearFlag(tid, "stalled"))}>Keep waiting</Button>
            <span className="text-meta">clears itself the moment the step finishes</span>
          </div>
        </div>
      ) : null}

      {att.kind === "untested" ? (
        <div className="mt-2 flex flex-col gap-2">
          {att.detail ? <div className="text-[12px] text-muted">{att.detail}</div> : null}
          <div className="flex flex-wrap items-center gap-2">
            <Link to={`/tasks/${tid}?tab=diff`}><Button size="sm" variant="secondary">Open the diff</Button></Link>
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(async () => { await act.merge(tid, true); })}>Merge anyway</Button>
            {onContinue ? <Button size="sm" variant="secondary" disabled={busy} onClick={onContinue}>Retry asking for a test</Button> : null}
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => run(() => act.clearFlag(tid, "untested"))}>Clear</Button>
          </div>
        </div>
      ) : null}

      {att.kind === "directive" ? (
        <div className="mt-2 flex flex-col gap-2">
          <div className="text-[12px] text-muted">The decide model audited the agent's recent activity after a reminder and thinks it may be breaking this standing order. Read what it did, then correct it or clear the flag.</div>
          {att.detail ? (
            <div>
              <button type="button" onClick={() => setShowDetail((v) => !v)} className="flex items-center gap-1 text-[12px] text-muted hover:text-fg">
                {showDetail ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} what the audit looked at
              </button>
              {showDetail ? <pre className="mono mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-surface p-2 text-muted">{att.detail}</pre> : null}
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            {isSession ? (
              <form className="flex min-w-[240px] flex-1 items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); if (text.trim()) run(async () => { await act.send(tid, text); await act.clearFlag(tid, "directive"); }); }}>
                <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="correct it: what to undo and what to do instead" className={inputCls} />
                <Button size="sm" variant="secondary" type="submit" disabled={busy || !text.trim()}>Send correction</Button>
              </form>
            ) : (
              <span className="text-[12px] text-muted">headless lane: cancel it and retry with the order written into the spec, or let it finish and check the diff</span>
            )}
            {onCancel ? <Button size="sm" variant="destructive" disabled={busy} onClick={onCancel}>Stop it</Button> : null}
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => act.clearFlag(tid, "directive"))}>False alarm, clear it</Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
