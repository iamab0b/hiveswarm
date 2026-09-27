import * as React from "react";
import { Link } from "react-router-dom";
import { Bot, Crown, KeyboardIcon, Sparkles, Timer } from "lucide-react";
import { agentColor, agentLabel, cn, elapsed, openStep, sessionOf } from "@/lib/utils";
import type { AgentStatus, FeedEntry, Task } from "@/lib/types";

export function AgentDot({ agent, className }: { agent?: string | null; className?: string }) {
  return <span className={cn("inline-block h-2 w-2 rounded-full", className)} style={{ background: agentColor(agent) }} />;
}

export function AgentChip({ agent, size = "md", lead, session }: { agent?: string | null; size?: "sm" | "md"; lead?: boolean; session?: boolean }) {
  const color = agentColor(agent);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border font-medium whitespace-nowrap",
        size === "sm" ? "h-5 px-1.5 text-[11px]" : "h-6 px-2 text-[12px]",
      )}
      style={{ color, borderColor: `color-mix(in oklab, ${color} 35%, transparent)`, background: `color-mix(in oklab, ${color} 10%, transparent)` }}
    >
      {lead ? <Crown className="h-3 w-3" /> : session ? <KeyboardIcon className="h-3 w-3" /> : <Bot className="h-3 w-3" />}
      {agentLabel(agent)}
    </span>
  );
}

export function TaskKindIcon({ t }: { t: Task }) {
  const s = sessionOf(t);
  if (t.kind === "session" && s.lead) return <Crown className="h-3.5 w-3.5 text-accent" />;
  if (t.kind === "session") return <KeyboardIcon className="h-3.5 w-3.5 text-success" />;
  return <Sparkles className="h-3.5 w-3.5 text-muted" />;
}

export function Mono({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn("mono", className)}>{children}</span>;
}

export function Id({ id, to, n = 8, className }: { id: string; to?: string; n?: number; className?: string }) {
  const el = <span className={cn("mono text-[12px] text-muted", className)}>{id.slice(0, n)}</span>;
  return to ? <Link to={to} className="hover:text-fg hover:underline underline-offset-2">{el}</Link> : el;
}

export function taskHref(t: Task): string {
  return t.kind === "session" ? `/sessions/${t.id}` : `/tasks/${t.id}`;
}

export function EmptyState({ icon, title, hint, action }: { icon?: React.ReactNode; title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="flex h-full min-h-[240px] flex-col items-center justify-center gap-2 text-center">
      {icon ? <div className="text-dim">{icon}</div> : null}
      <div className="text-[14px] font-medium text-fg">{title}</div>
      {hint ? <div className="max-w-md text-[12.5px] text-muted">{hint}</div> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function SectionTitle({ children, right, className }: { children: React.ReactNode; right?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-center justify-between px-1 pb-2", className)}>
      <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-dim">{children}</div>
      {right}
    </div>
  );
}

export function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "success" | "danger" | "warn" | "info" | "accent" }) {
  const c = tone ? `text-${tone}` : "text-fg";
  return (
    <div className="flex items-baseline gap-1.5">
      <span className={cn("num text-[15px] font-semibold", c)}>{value}</span>
      <span className="text-[11.5px] text-dim">{label}</span>
    </div>
  );
}

/** "on Bash: pytest -q · 4m02s" for a lane in the middle of a tool call; amber after two minutes, red after five. */
export function StepTimer({ entries, agent, now, className, compact }: { entries: FeedEntry[]; agent: string | null | undefined; now: number; className?: string; compact?: boolean }) {
  const step = openStep(entries, agent);
  if (!step) return null;
  const secs = Math.max(0, now - step.since);
  const tone = secs >= 300 ? "text-danger border-danger/40 bg-danger/8" : secs >= 120 ? "text-warn border-warn/40 bg-warn/8" : "text-muted border-border bg-surface-2";
  return (
    <span className={cn("inline-flex max-w-full items-center gap-1.5 rounded-md border px-1.5 py-0.5 text-[11px]", tone, className)} title={step.tool}>
      <Timer className="h-3 w-3 shrink-0" />
      {!compact ? <span className="mono truncate">{step.tool}</span> : null}
      <span className="num shrink-0">{elapsed(step.since, now)}</span>
    </span>
  );
}

const STATUS_TONE: Record<AgentStatus, string> = {
  healthy: "border-success/35 bg-success/10 text-success",
  slow: "border-warn/40 bg-warn/10 text-warn",
  failing: "border-danger/40 bg-danger/10 text-danger",
  unknown: "border-border bg-surface-2 text-dim",
};

export function StatusBadge({ status, className }: { status: AgentStatus; className?: string }) {
  return <span className={cn("inline-flex h-5 items-center rounded-sm border px-1.5 text-[11px] font-medium", STATUS_TONE[status] || STATUS_TONE.unknown, className)}>{status}</span>;
}
