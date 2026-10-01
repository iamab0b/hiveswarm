import * as React from "react";
import { cn } from "@/lib/utils";

/** The four signals of the design: needs you (accent), finished (success), failed (danger), everything else (neutral). */
export type Tone = "neutral" | "accent" | "success" | "danger" | "dim" | "info" | "warn" | "fg";

const DOT: Record<Tone, string> = {
  neutral: "bg-muted",
  dim: "bg-dim",
  info: "bg-muted",
  fg: "bg-fg",
  accent: "bg-accent",
  warn: "bg-accent",
  success: "bg-success",
  danger: "bg-danger",
};

const TEXT: Record<Tone, string> = {
  neutral: "text-muted",
  dim: "text-dim",
  info: "text-muted",
  fg: "text-fg",
  accent: "text-fg",
  warn: "text-fg",
  success: "text-fg",
  danger: "text-fg",
};

/** A dot and a word. `outline` draws a hairline chip around it for places that need an edge (table cells, chips in a sentence). */
export function Badge({ tone = "neutral", className, children, dot = true, live, outline, ...props }: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone; dot?: boolean; live?: boolean; outline?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1.5 whitespace-nowrap text-[12px] font-medium leading-none",
        outline && "rounded-sm border border-border bg-surface px-1.5",
        TEXT[tone],
        className,
      )}
      {...props}
    >
      {dot ? <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", DOT[tone], live && "live-dot")} /> : null}
      {children}
    </span>
  );
}

export function stateTone(state: string): Tone {
  switch (state) {
    case "done": return "success";
    case "failed": case "blocked": return "danger";
    default: return "neutral";
  }
}

export function StateBadge({ state, live, outline, attention }: { state: string; live?: boolean; outline?: boolean; attention?: boolean }) {
  const active = state === "running" || state === "verifying" || state === "claimed";
  const tone: Tone = attention ? "accent" : stateTone(state);
  return (
    <Badge tone={tone} live={live && active && !attention} outline={outline}>
      {attention ? "needs you" : state}
    </Badge>
  );
}
