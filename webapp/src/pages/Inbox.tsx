import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "motion/react";
import { CheckCheck, Inbox as InboxIcon, RotateCcw, Trash2 } from "lucide-react";
import { useStore } from "@/lib/store";
import type { InboxItem } from "@/lib/types";
import { age, attentionTone, cn, firstLine, short } from "@/lib/utils";
import { AgentChip, EmptyState } from "@/components/bits";
import { AttentionPanel } from "@/components/Attention";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ContinueDialog, RetryDialog } from "@/components/dialogs";
import { act } from "@/components/actions";

export function InboxList({ compact, onNavigate }: { compact?: boolean; onNavigate?: () => void }) {
  const inbox = useStore((s) => s.inbox);
  const taskById = useStore((s) => s.taskById);
  const nav = useNavigate();
  const [cont, setCont] = useState<string | null>(null);
  const [retry, setRetry] = useState<string | null>(null);
  const groups = useMemo(() => {
    const live = inbox.filter((i) => i.attention.kind !== "failed");
    const failed = inbox.filter((i) => i.attention.kind === "failed");
    return { live, failed };
  }, [inbox]);
  const open = (it: InboxItem) => {
    onNavigate?.();
    nav(it.kind === "session" ? `/sessions/${it.id}` : `/tasks/${it.id}`);
  };
  if (!inbox.length) {
    return <EmptyState icon={<CheckCheck className="h-8 w-8" />} title="Nothing needs you" hint="Approvals, questions, sessions waiting for input, standing-order flags, usage limits and failures show up here the moment they happen." />;
  }
  return (
    <div className="grid gap-2">
      <AnimatePresence initial={false}>
        {groups.live.map((it) => (
          <motion.div key={`${it.id}-${it.attention.kind}`} layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.98 }} className="card p-3">
            <div className="mb-2 flex items-center gap-2">
              <AgentChip agent={it.agent} size="sm" session={it.kind === "session"} />
              <button className="mono text-[12px] text-muted hover:text-fg" onClick={() => open(it)}>{short(it.id)}</button>
              <span className="text-[11.5px] text-dim">{it.project}</span>
              <span className="flex-1 truncate text-[12px] text-muted">{firstLine(it.spec, 70)}</span>
              <span className="text-[11px] text-dim">{age(it.attention.since || it.updated_at)}</span>
              <Button size="xs" variant="ghost" onClick={() => open(it)}>open</Button>
            </div>
            <AttentionPanel tid={it.id} att={it.attention} compact={compact} isSession={it.kind === "session"} onContinue={() => setCont(it.id)} onFinish={() => act.finish(it.id)} onCancel={() => { const t = taskById[it.id]; if (t) act.cancel(t); }} />
          </motion.div>
        ))}
      </AnimatePresence>
      {groups.failed.length ? (
        <div className="mt-2">
          <div className="mb-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-dim">Failed · {groups.failed.length}</div>
          <div className="grid gap-1.5">
            {groups.failed.map((it) => (
              <div key={it.id} className="card flex items-center gap-2 px-3 py-2">
                <Badge tone={attentionTone(it.attention.kind)}>failed</Badge>
                <AgentChip agent={it.agent} size="sm" session={it.kind === "session"} />
                <Link to={it.kind === "session" ? `/sessions/${it.id}` : `/tasks/${it.id}`} className="mono text-[12px] text-muted hover:text-fg" onClick={onNavigate}>{short(it.id)}</Link>
                <span className="min-w-0 flex-1 truncate text-[12px] text-muted" title={it.attention.summary}>{firstLine(it.attention.summary, 90)}</span>
                <span className="text-[11px] text-dim">{age(it.updated_at)}</span>
                <Button size="xs" variant="ghost" onClick={() => setRetry(it.id)}><RotateCcw className="h-3 w-3" /> retry</Button>
                <Button size="xs" variant="ghost" className="text-danger" onClick={() => act.remove(it.id)}><Trash2 className="h-3 w-3" /></Button>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      <ContinueDialog task={cont ? taskById[cont] || null : null} open={!!cont} onOpenChange={(o) => !o && setCont(null)} />
      <RetryDialog task={retry ? taskById[retry] || null : null} open={!!retry} onOpenChange={(o) => !o && setRetry(null)} />
    </div>
  );
}

export default function InboxPage() {
  const inbox = useStore((s) => s.inbox);
  const live = inbox.filter((i) => i.attention.kind !== "failed").length;
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl px-6 py-5">
        <div className="mb-4 flex items-end justify-between">
          <div>
            <h1 className="flex items-center gap-2 text-[18px] font-semibold tracking-tight"><InboxIcon className="h-4.5 w-4.5 text-accent" /> Inbox</h1>
            <p className="mt-0.5 text-[12.5px] text-muted">{live ? `${live} thing${live === 1 ? "" : "s"} waiting on you` : "Everything that needs a human, most urgent first."} <span className={cn("ml-2 text-dim")}>press <kbd className="key">n</kbd> anywhere to jump to the next one</span></p>
          </div>
        </div>
        <InboxList />
      </div>
    </div>
  );
}
