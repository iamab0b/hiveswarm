import * as React from "react";
import { cn } from "@/lib/utils";

type Tone = "dim" | "info" | "warn" | "accent" | "success" | "danger" | "fg";

const TONES: Record<Tone, string> = {
  dim: "bg-surface-3 text-muted border-border",
  info: "bg-info/12 text-info border-info/25",
  warn: "bg-warn/12 text-warn border-warn/30",
  accent: "bg-accent/12 text-accent border-accent/30",
  success: "bg-success/12 text-success border-success/25",
  danger: "bg-danger/12 text-danger border-danger/30",
  fg: "bg-fg text-bg border-fg",
};

export function Badge({ tone = "dim", className, children, dot, ...props }: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone; dot?: boolean }) {
  return (
    <span
      className={cn("inline-flex items-center gap-1 rounded-sm border px-1.5 h-5 text-[11px] font-medium leading-none whitespace-nowrap", TONES[tone], className)}
      {...props}
    >
      {dot ? <span className="h-1.5 w-1.5 rounded-full bg-current" /> : null}
      {children}
    </span>
  );
}

export function StateBadge({ state, live }: { state: string; live?: boolean }) {
  const tone: Tone = ({
    pending: "dim", classifying: "dim", classified: "warn", assigned: "warn", claimed: "info", running: "info",
    verifying: "accent", done: "success", failed: "danger", blocked: "danger", abandoned: "dim",
  } as Record<string, Tone>)[state] || "dim";
  return (
    <Badge tone={tone} className={cn(live && "[&>span]:live-dot")} dot={state === "running" || state === "verifying"}>
      {state}
    </Badge>
  );
}
