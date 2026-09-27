import { useState } from "react";
import { AlertTriangle, Check, ChevronDown, ChevronRight, HelpCircle, Hourglass, MessageSquare, ScrollText, ShieldAlert, Timer, X } from "lucide-react";
import { motion } from "motion/react";
import type { Attention } from "@/lib/types";
import { attentionLabel, cn } from "@/lib/utils";
import { Button } from "./ui/button";
import { Input } from "./ui/fields";
import { act } from "./actions";

const ICON: Record<string, React.ReactNode> = {
  permission: <ShieldAlert className="h-4 w-4" />,
  question: <HelpCircle className="h-4 w-4" />,
  input: <MessageSquare className="h-4 w-4" />,
  usage_limit: <Hourglass className="h-4 w-4" />,
  failed: <AlertTriangle className="h-4 w-4" />,
  handoff: <AlertTriangle className="h-4 w-4" />,
  directive: <ScrollText className="h-4 w-4" />,
  stalled: <Timer className="h-4 w-4" />,
};

const TONE: Record<string, string> = {
  permission: "border-danger/40 bg-danger/8 text-danger",
  question: "border-warn/40 bg-warn/8 text-warn",
  input: "border-success/35 bg-success/8 text-success",
  usage_limit: "border-info/40 bg-info/8 text-info",
  failed: "border-danger/40 bg-danger/8 text-danger",
  handoff: "border-warn/40 bg-warn/8 text-warn",
  directive: "border-warn/50 bg-warn/8 text-warn",
  stalled: "border-warn/50 bg-warn/8 text-warn",
};

export function AttentionPanel({ tid, att, compact, isSession = true, onContinue, onFinish, onCancel }: {
  tid: string;
  att: Attention;
  compact?: boolean;
  isSession?: boolean;
  onContinue?: () => void;
  onFinish?: () => void;
  onCancel?: () => void;
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
  return (
    <motion.div
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn("rounded-lg border px-3 py-2.5", TONE[att.kind] || "border-border bg-surface-2 text-fg")}
    >
      <div className="flex items-start gap-2.5">
        <div className="mt-0.5 shrink-0">{ICON[att.kind]}</div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-[12px] font-semibold uppercase tracking-wide">{attentionLabel(att)}</span>
            {att.risk ? <span className="text-[11px] opacity-80">{att.risk} risk</span> : null}
          </div>
          <div className={cn("mt-0.5 break-words text-fg", compact ? "text-[12.5px]" : "text-[13.5px] font-medium")}>{att.summary}</div>
          {att.kind === "permission" && att.detail && att.detail !== att.summary.split(": ").slice(1).join(": ") ? (
            <pre className="mono mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-bg/60 p-2 text-[11.5px] text-muted">{att.detail}</pre>
          ) : null}

          {att.kind === "permission" ? (
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              <Button variant="success" size="sm" disabled={busy} onClick={() => run(() => act.approve(tid))} kbd="y">
                <Check className="h-3.5 w-3.5" /> Approve
              </Button>
              <Button variant="danger" size="sm" disabled={busy} onClick={() => run(() => act.deny(tid))} kbd="d">
                <X className="h-3.5 w-3.5" /> Deny
              </Button>
              <form
                className="flex min-w-[220px] flex-1 items-center gap-1.5"
                onSubmit={(e) => { e.preventDefault(); if (text.trim()) run(() => act.deny(tid, text)); }}
              >
                <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="deny with a message: what to do instead…" className="h-7 text-[12px]" />
                <Button size="sm" variant="outline" type="submit" disabled={busy || !text.trim()}>Send</Button>
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
                <Input value={text} onChange={(e) => setText(e.target.value)} placeholder={options.length ? "or type your own answer…" : "type your answer…"} className="h-7 text-[12px]" />
                <Button size="sm" variant="outline" type="submit" disabled={busy || !text.trim()}>Answer</Button>
              </form>
            </div>
          ) : null}

          {att.kind === "input" ? (
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              <form className="flex min-w-[240px] flex-1 items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); if (text.trim()) run(() => act.send(tid, text)); }}>
                <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="next instruction…" className="h-7 text-[12px]" />
                <Button size="sm" variant="outline" type="submit" disabled={busy || !text.trim()}>Send</Button>
              </form>
              {onFinish ? <Button size="sm" variant="accent" onClick={onFinish} kbd="F">Finish</Button> : null}
            </div>
          ) : null}

          {att.kind === "usage_limit" ? (
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              {onContinue ? <Button size="sm" variant="accent" onClick={onContinue} kbd="o">Continue on another agent</Button> : null}
              <span className="text-[11.5px] text-muted">its work is saved and handed over; or wait and reopen it later</span>
            </div>
          ) : null}

          {att.kind === "stalled" ? (
            <div className="mt-2 flex flex-col gap-2">
              <div className="text-[11.5px] text-muted">{att.detail || "One step has taken much longer than usual. Either it is a legitimately long command or the agent is stuck."}</div>
              <div className="flex flex-wrap items-center gap-2">
                {onCancel ? <Button size="sm" variant="danger" disabled={busy} onClick={onCancel}>Stop it</Button> : null}
                {isSession && onContinue ? <Button size="sm" variant="accent" disabled={busy} onClick={onContinue} kbd="o">Continue on another agent</Button> : null}
                <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => act.clearFlag(tid, "stalled"))}>It's fine — keep waiting</Button>
                <span className="text-[11px] text-dim">clears itself the moment the step finishes</span>
              </div>
            </div>
          ) : null}

          {att.kind === "directive" ? (
            <div className="mt-2 flex flex-col gap-2">
              <div className="text-[11.5px] text-muted">The decide model audited the agent's recent activity after a reminder and thinks it may be breaking this standing order. Read what it did, then correct it or clear the flag.</div>
              {att.detail ? (
                <div>
                  <button onClick={() => setShowDetail((v) => !v)} className="flex items-center gap-1 text-[11.5px] text-muted hover:text-fg">
                    {showDetail ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} what the audit looked at
                  </button>
                  {showDetail ? <pre className="mono mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-bg/60 p-2 text-[11px] text-muted">{att.detail}</pre> : null}
                </div>
              ) : null}
              <div className="flex flex-wrap items-center gap-2">
                {isSession ? (
                  <form className="flex min-w-[240px] flex-1 items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); if (text.trim()) run(async () => { await act.send(tid, text); await act.clearFlag(tid, "directive"); }); }}>
                    <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="correct it: what to undo and what to do instead…" className="h-7 text-[12px]" />
                    <Button size="sm" variant="outline" type="submit" disabled={busy || !text.trim()}>Send correction</Button>
                  </form>
                ) : (
                  <span className="text-[11.5px] text-muted">headless lane — cancel it and retry with the order written into the spec, or let it finish and check the diff</span>
                )}
                {onCancel ? <Button size="sm" variant="danger" disabled={busy} onClick={onCancel}>Stop it</Button> : null}
                <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => act.clearFlag(tid, "directive"))}>False alarm — clear</Button>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </motion.div>
  );
}
