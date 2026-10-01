import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "motion/react";
import { useStore } from "@/lib/store";
import type { InboxItem } from "@/lib/types";
import { age, firstLine, short } from "@/lib/utils";
import { AgentChip, EmptyState, PageHeader } from "@/components/bits";
import { AttentionPanel } from "@/components/Attention";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
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
    return <EmptyState title="Nothing needs you" hint="Approvals, questions, sessions waiting for input, standing-order flags, usage limits and failures appear here the moment they happen." />;
  }
  return (
    <div className="grid gap-3">
      <AnimatePresence initial={false}>
        {groups.live.map((it) => (
          <motion.div key={`${it.id}-${it.attention.kind}`} layout initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.15, ease: "easeOut" }} className="card p-3">
            <div className="mb-2.5 flex items-center gap-2">
              <AgentChip agent={it.agent} size="sm" session={it.kind === "session"} />
              <button type="button" className="mono text-muted hover:text-fg" onClick={() => open(it)}>{short(it.id)}</button>
              <span className="text-meta">{it.project}</span>
              <span className="min-w-0 flex-1 truncate text-[12px] text-muted">{firstLine(it.spec, 70)}</span>
              <span className="num text-meta">{age(it.attention.since || it.updated_at)}</span>
              <Button size="xs" variant="ghost" onClick={() => open(it)}>open</Button>
            </div>
            <AttentionPanel tid={it.id} att={it.attention} compact={compact} isSession={it.kind === "session"} onContinue={() => setCont(it.id)} onFinish={() => act.finish(it.id)} onCancel={() => { const t = taskById[it.id]; if (t) act.cancel(t); }} />
          </motion.div>
        ))}
      </AnimatePresence>
      {groups.failed.length ? (
        <section className="mt-2">
          <div className="text-label mb-2">Failed <span className="num text-dim">{groups.failed.length}</span></div>
          <Card className="divide-y divide-border">
            {groups.failed.map((it) => (
              <div key={it.id} className="row">
                <Badge tone="danger">failed</Badge>
                <AgentChip agent={it.agent} size="sm" session={it.kind === "session"} />
                <Link to={it.kind === "session" ? `/sessions/${it.id}` : `/tasks/${it.id}`} className="mono text-muted hover:text-fg" onClick={onNavigate}>{short(it.id)}</Link>
                <span className="min-w-0 flex-1 truncate text-[12px] text-muted" title={it.attention.summary}>{firstLine(it.attention.summary, 90)}</span>
                <span className="num text-meta">{age(it.updated_at)}</span>
                <Button size="xs" variant="ghost" onClick={() => setRetry(it.id)}>retry</Button>
                <Button size="xs" variant="ghost" className="hover:text-danger" onClick={() => act.remove(it.id)}>remove</Button>
              </div>
            ))}
          </Card>
        </section>
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
        <PageHeader
          title="Inbox"
          count={live || undefined}
          subtitle={<>Everything that needs a human, most urgent first. Press <kbd className="key">n</kbd> anywhere to jump to the next one.</>}
        />
        <InboxList />
      </div>
    </div>
  );
}
