import * as React from "react";
import { Link } from "react-router-dom";
import { Crown, TerminalSquare, Timer } from "lucide-react";
import { agentColor, agentLabel, cn, elapsed, openStep, sessionOf } from "@/lib/utils";
import type { AgentStatus, FeedEntry, Task } from "@/lib/types";
import { Badge, type Tone } from "./ui/badge";

export function AgentDot({ agent, className }: { agent?: string | null; className?: string }) {
  return <span className={cn("inline-block h-1.5 w-1.5 shrink-0 rounded-full", className)} style={{ background: agentColor(agent) }} />;
}

/** An agent's name in a neutral chip; the hue dot is the only colour. */
export function AgentChip({ agent, size = "md", lead, session, className }: { agent?: string | null; size?: "sm" | "md"; lead?: boolean; session?: boolean; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-sm border border-border bg-surface font-medium text-fg", size === "sm" ? "h-5 px-1.5 text-[11px]" : "h-6 px-2 text-[12px]", className)}>
      <AgentDot agent={agent} />
      {agentLabel(agent)}
      {lead ? <Crown className="h-3 w-3 text-muted" /> : session ? <TerminalSquare className="h-3 w-3 text-muted" /> : null}
    </span>
  );
}

/** The kind of a task: the lead's crown, a session's terminal, or nothing for headless work. */
export function TaskKindIcon({ t, className }: { t: Task; className?: string }) {
  const s = sessionOf(t);
  if (t.kind === "session" && s.lead) return <Crown className={cn("h-3.5 w-3.5 text-muted", className)} />;
  if (t.kind === "session") return <TerminalSquare className={cn("h-3.5 w-3.5 text-muted", className)} />;
  return null;
}

export function Mono({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn("mono", className)}>{children}</span>;
}

export function Id({ id, to, n = 8, className }: { id: string; to?: string; n?: number; className?: string }) {
  const el = <span className={cn("mono text-muted", className)}>{id.slice(0, n)}</span>;
  return to ? <Link to={to} className="hover:text-fg">{el}</Link> : el;
}

export function taskHref(t: Task): string {
  return t.kind === "session" ? `/sessions/${t.id}` : `/tasks/${t.id}`;
}

/** Title row of a page: title, optional count and subtitle, actions on the right. No icon. */
export function PageHeader({ title, count, subtitle, right, className }: { title: React.ReactNode; count?: React.ReactNode; subtitle?: React.ReactNode; right?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("mb-5 flex flex-wrap items-start gap-3", className)}>
      <div className="min-w-0">
        <h1 className="text-title flex items-center gap-2">{title}{count !== undefined && count !== null ? <span className="num text-[13px] font-normal text-dim">{count}</span> : null}</h1>
        {subtitle ? <p className="mt-0.5 max-w-3xl text-[12px] leading-[18px] text-muted">{subtitle}</p> : null}
      </div>
      {right ? <div className="ml-auto flex flex-wrap items-center gap-2">{right}</div> : null}
    </div>
  );
}

export function EmptyState({ title, hint, action, className }: { icon?: React.ReactNode; title: string; hint?: string; action?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex h-full min-h-[200px] flex-col items-center justify-center gap-1 px-6 text-center", className)}>
      <div className="text-[13px] font-medium text-fg">{title}</div>
      {hint ? <div className="max-w-md text-[12px] leading-[18px] text-muted">{hint}</div> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

export function SectionTitle({ children, right, className }: { children: React.ReactNode; right?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("mb-2 flex items-center justify-between", className)}>
      <div className="text-label">{children}</div>
      {right}
    </div>
  );
}

export function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "success" | "danger" | "accent" | "warn" | "info" }) {
  const c = tone === "success" ? "text-success" : tone === "danger" ? "text-danger" : tone === "accent" || tone === "warn" ? "text-accent" : "text-fg";
  return (
    <div className="flex items-baseline gap-1.5">
      <span className={cn("num text-[15px] font-semibold", c)}>{value}</span>
      <span className="text-meta">{label}</span>
    </div>
  );
}

/** "Bash: pytest -q · 4m02s" for a lane in the middle of a tool call; the time turns red after five minutes. */
export function StepTimer({ entries, agent, now, className, compact }: { entries: FeedEntry[]; agent: string | null | undefined; now: number; className?: string; compact?: boolean }) {
  const step = openStep(entries, agent);
  if (!step) return null;
  const secs = Math.max(0, now - step.since);
  return (
    <span className={cn("inline-flex max-w-full items-center gap-1.5 text-[11px] text-muted", secs >= 300 && "text-danger", className)} title={step.tool}>
      <Timer className="h-3 w-3 shrink-0" />
      {!compact ? <span className="mono truncate text-[11px]">{step.tool}</span> : null}
      <span className="num shrink-0">{elapsed(step.since, now)}</span>
    </span>
  );
}

const STATUS_TONE: Record<AgentStatus, Tone> = { healthy: "success", slow: "accent", failing: "danger", unknown: "dim" };

export function StatusBadge({ status, className, outline }: { status: AgentStatus; className?: string; outline?: boolean }) {
  return <Badge tone={STATUS_TONE[status] || "dim"} outline={outline} className={className}>{status}</Badge>;
}
