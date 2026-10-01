import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { Attention, FeedEntry, SessionInfo, Task, TaskFlag, TaskState } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Hue dots only (6 px next to a name, chart series); never a border, a background or a heading. */
export const AGENT_COLORS: Record<string, string> = {
  claude_code: "#d9843f",
  codex: "#3aa981",
  cursor: "#8b7cf6",
  prime_agent: "#5aa0a0",
  local_direct: "#5aa0a0",
  antigravity: "#4f8ee6",
  gemini: "#4f8ee6",
  opencode: "#c9a227",
};

export const AGENT_LABELS: Record<string, string> = {
  claude_code: "Claude Code",
  codex: "Codex",
  cursor: "Cursor",
  prime_agent: "Prime Agent",
  local_direct: "Local",
  antigravity: "Antigravity",
  gemini: "Gemini",
  opencode: "OpenCode",
};

export function agentColor(agent?: string | null): string {
  return AGENT_COLORS[agent || ""] || "#8c8c96";
}

export function agentLabel(agent?: string | null): string {
  if (!agent) return "unassigned";
  return AGENT_LABELS[agent] || agent;
}

export const TERMINAL: TaskState[] = ["done", "failed", "abandoned"];
export const WORKING: TaskState[] = ["assigned", "claimed", "running", "verifying"];

export function isTerminal(s: TaskState) {
  return TERMINAL.includes(s);
}

export function sessionOf(t: Task | null | undefined): SessionInfo {
  if (!t || t.kind !== "session" || !t.session) return {};
  if (typeof t.session === "string") {
    try {
      return JSON.parse(t.session) as SessionInfo;
    } catch {
      return {};
    }
  }
  return t.session;
}

export function taskAgent(t: Task): string | null {
  return t.claimed_by || sessionOf(t).agent || null;
}

export function taskFlags(t: Task | null | undefined): TaskFlag[] {
  if (!t || !t.flags) return [];
  if (typeof t.flags === "string") {
    try {
      const v = JSON.parse(t.flags);
      return Array.isArray(v) ? (v as TaskFlag[]) : [];
    } catch {
      return [];
    }
  }
  return t.flags;
}

export function flagAttention(t: Task | null | undefined): Attention | null {
  const flags = taskFlags(t);
  if (!flags.length) return null;
  const f = flags[flags.length - 1];
  const kind = (f.kind === "directive" || f.kind === "stalled" || f.kind === "untested" ? f.kind : "failed") as Attention["kind"];
  return { kind, summary: f.summary, detail: f.detail, since: f.since, ref: f.ref };
}

/** The untested flag outlives the task: a done task keeps it until someone reviews and merges or clears it. */
export function untestedFlag(t: Task | null | undefined): Attention | null {
  const f = taskFlags(t).find((x) => x.kind === "untested");
  return f ? { kind: "untested", summary: f.summary, detail: f.detail, since: f.since, ref: f.ref } : null;
}

export interface OpenStep { tool: string; since: number; }

const STEP_END = new Set(["result", "err"]);

/** The tool call an agent is in the middle of, from its event stream: the last `tool` entry with no later result. */
export function openStep(entries: FeedEntry[], agent: string | null | undefined): OpenStep | null {
  if (!agent) return null;
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    const { agent: a, kind } = splitSource(e.source);
    if (a !== agent) continue;
    if (kind === "tool") return { tool: firstLine(e.chunk, 90), since: e.ts };
    if (STEP_END.has(kind)) return null;
  }
  return null;
}

export function fmtSecs(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (v >= 3600) return `${(v / 3600).toFixed(1)}h`;
  if (v >= 90) return `${(v / 60).toFixed(1)}m`;
  return `${Math.round(v)}s`;
}

export function fmtPct(v: number | null | undefined): string {
  return v === null || v === undefined ? "–" : `${Math.round(v * 100)}%`;
}

export function liveAttention(t: Task): Attention | null {
  if (isTerminal(t.state)) return null;
  const a = sessionOf(t).attention;
  return a || flagAttention(t);
}

export function isLive(t: Task): boolean {
  if (t.kind === "session") return !isTerminal(t.state);
  return WORKING.includes(t.state);
}

export function firstLine(s: string | null | undefined, n = 90): string {
  if (!s) return "";
  const line = s.trim().split("\n")[0] || "";
  return line.length > n ? line.slice(0, n) + "…" : line;
}

export function age(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "";
  const d = Math.max(0, Math.floor(now - ts));
  if (d < 60) return `${d}s`;
  if (d < 3600) return `${Math.floor(d / 60)}m`;
  if (d < 86400) return `${Math.floor(d / 3600)}h`;
  return `${Math.floor(d / 86400)}d`;
}

export function elapsed(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "";
  const d = Math.max(0, Math.floor(now - ts));
  const m = Math.floor(d / 60);
  const s = d % 60;
  if (m >= 60) return `${Math.floor(m / 60)}h ${m % 60}m`;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

export function clock(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function short(id: string, n = 8) {
  return id.slice(0, n);
}

export const STATE_TONE: Record<string, string> = {
  pending: "dim",
  classifying: "dim",
  classified: "warn",
  assigned: "warn",
  claimed: "info",
  running: "info",
  verifying: "accent",
  done: "success",
  failed: "danger",
  blocked: "danger",
  abandoned: "dim",
};

export type FeedKind =
  | "msg" | "tool" | "result" | "err" | "info" | "status" | "think" | "out" | "you"
  | "daemon" | "handoff" | "good" | "bad" | "note" | "ask" | "warn" | "verifier";

export function splitSource(source: string): { agent: string | null; kind: string } {
  const i = source.indexOf(":");
  if (i > 0) return { agent: source.slice(0, i), kind: source.slice(i + 1) };
  const legacy: Record<string, string> = { stdout: "out", stderr: "err", worker: "status" };
  if (legacy[source]) return { agent: null, kind: legacy[source] };
  return { agent: null, kind: source };
}

export function daemonKind(chunk: string): FeedKind {
  const c = chunk || "";
  if (c.startsWith("handoff to")) return "handoff";
  if (/^(verifier result: pass|verifier result: soft|done|merged)/.test(c)) return "good";
  if (/^(verifier result:|failed|requeued \(|worker error)/.test(c)) return "bad";
  if (/^(needs your approval|question for you|usage limit)/.test(c)) return "ask";
  if (c.startsWith("overlap:") || c.startsWith("directive check failed") || c.startsWith("stalled:")) return "warn";
  if (c.startsWith("you")) return "you";
  if (/^(enqueued|classified|retried|requeued:|cancelled|session requested|lead session requested|session reopened|auto-approved|finish requested|routed to|continued in|handing off|standing order|reminded \(|directive check passed|flag cleared|directive flag cleared|stalled flag cleared|moving again)/.test(c)) return "note";
  return "daemon";
}

export function statusKind(chunk: string): FeedKind {
  const c = chunk || "";
  if (/^(exited|finished but made no changes|worker crashed|timed out|session host crashed)/.test(c)) return "bad";
  return "status";
}

export function classifyEntry(source: string, chunk: string): { agent: string | null; kind: FeedKind } {
  const { agent, kind } = splitSource(source);
  if (kind === "daemon") return { agent: null, kind: daemonKind(chunk) };
  if (kind === "status") return { agent, kind: statusKind(chunk) };
  return { agent, kind: kind as FeedKind };
}

export function attentionLabel(a: Attention): string {
  switch (a.kind) {
    case "permission": return "Needs approval";
    case "question": return "Asks you";
    case "input": return "Waiting for you";
    case "usage_limit": return "Usage limit";
    case "handoff": return "Handoff";
    case "failed": return "Failed";
    case "directive": return "Standing order";
    case "stalled": return "Stalled";
    case "untested": return "Untested";
    default: return a.kind;
  }
}

export function attentionTone(kind: string): "warn" | "danger" | "success" | "info" | "dim" {
  switch (kind) {
    case "permission": return "danger";
    case "question": return "warn";
    case "input": return "success";
    case "usage_limit": return "info";
    case "failed": return "danger";
    case "directive": return "warn";
    case "stalled": return "warn";
    case "untested": return "warn";
    default: return "dim";
  }
}
