import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ArrowUp, ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { AdvisorMessage, AdvisorState } from "@/lib/types";
import { age, cn } from "@/lib/utils";
import { Button } from "./ui/button";
import { Textarea } from "./ui/fields";
import { Markdown } from "./ui/markdown";
import { EmptyState } from "./bits";

export function useAdvisor(project: string | null | undefined) {
  const [state, setState] = useState<AdvisorState | null>(null);
  const reload = useCallback(() => {
    if (!project) return Promise.resolve();
    return api.advisor(project).then(setState).catch(() => undefined);
  }, [project]);
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const tick = async () => {
      if (!alive) return;
      await reload();
      timer = window.setTimeout(tick, document.hidden ? 10000 : state?.thinking ? 1000 : 3000);
    };
    tick();
    return () => { alive = false; if (timer) window.clearTimeout(timer); };
  }, [reload, state?.thinking]);
  return { state, reload };
}

type Group = { kind: "you"; m: AdvisorMessage } | { kind: "text"; m: AdvisorMessage } | { kind: "tools"; items: AdvisorMessage[]; id: string } | { kind: "error"; m: AdvisorMessage };

function group(messages: AdvisorMessage[]): Group[] {
  const out: Group[] = [];
  let tools: AdvisorMessage[] = [];
  const flush = () => { if (tools.length) { out.push({ kind: "tools", items: tools, id: tools[0].id }); tools = []; } };
  for (const m of messages) {
    if (m.role === "you") { flush(); out.push({ kind: "you", m }); continue; }
    if (m.kind === "tool" || m.kind === "result") { tools.push(m); continue; }
    flush();
    out.push(m.kind === "error" ? { kind: "error", m } : { kind: "text", m });
  }
  flush();
  return out;
}

function Speaker({ who }: { who: "you" | "advisor" }) {
  return <div className={cn("text-label w-14 shrink-0 pt-0.5 text-right", who === "you" && "text-fg")}>{who === "you" ? "You" : "Advisor"}</div>;
}

function ToolRow({ items }: { items: AdvisorMessage[] }) {
  const [open, setOpen] = useState(false);
  const calls = items.filter((i) => i.kind === "tool");
  return (
    <div className="flex gap-4">
      <div className="w-14 shrink-0" />
      <div className="min-w-0 flex-1">
        <button type="button" onClick={() => setOpen((v) => !v)} className="mono flex items-center gap-1.5 rounded-sm text-dim hover:text-muted">
          {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          {calls.length} tool call{calls.length === 1 ? "" : "s"} · {calls.map((c) => c.text.replace(/^mcp__hiveswarm__/, "").split(":")[0]).slice(0, 4).join(", ")}{calls.length > 4 ? ", …" : ""}
        </button>
        {open ? (
          <div className="mt-1.5 grid gap-1 rounded-md border border-border bg-surface-2 p-2">
            {items.map((it) => (
              <div key={it.id} className={cn("mono whitespace-pre-wrap", it.kind === "tool" ? "text-fg" : "text-muted")}>{it.kind === "tool" ? "▸ " : "  "}{it.text.slice(0, 800)}</div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** The advisor pane: a conversation about the plan and the swarm that never interrupts the lead. */
export function AdvisorPane({ project, state, reload }: { project: string; state: AdvisorState | null; reload: () => Promise<void> }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const messages = state?.messages || [];
  const groups = group(messages);
  const lastId = messages.length ? messages[messages.length - 1].id : "";
  useLayoutEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }); }, [lastId, state?.thinking]);
  const send = async () => {
    const v = text.trim();
    if (!v) return;
    setSending(true);
    try {
      await api.advisorTalk(project, v);
      setText("");
      await reload();
    } catch (e) {
      toast.error(String((e as Error).message));
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  };
  return (
    <div className="flex min-h-0 flex-1 flex-col" data-advisor-chat>
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-[760px] flex-col gap-4 px-6 py-6">
          {!messages.length ? (
            <EmptyState title="Ask the advisor about the plan" hint="It reads the swarm's state and the plan and answers without touching the lead. When you want the swarm to change course, say so: it pauses the project, briefs the lead, and the lead resumes once it has adjusted." />
          ) : null}
          {groups.map((g) => {
            if (g.kind === "you") return <div key={g.m.id} className="flex gap-4"><Speaker who="you" /><div className="min-w-0 flex-1 whitespace-pre-wrap text-[13px] leading-5 text-fg">{g.m.text}</div></div>;
            if (g.kind === "text") return <div key={g.m.id} className="flex gap-4"><Speaker who="advisor" /><Markdown text={g.m.text} className="min-w-0 flex-1" /></div>;
            if (g.kind === "error") return <div key={g.m.id} className="flex gap-4"><div className="w-14 shrink-0" /><div className="min-w-0 flex-1 text-[12px] text-danger">{g.m.text}</div></div>;
            return <ToolRow key={g.id} items={g.items} />;
          })}
          {state?.thinking ? <div className="flex gap-4"><div className="w-14 shrink-0" /><div className="flex items-center gap-2 text-[12px] text-dim"><Loader2 className="h-3 w-3 animate-spin" /> thinking{state.host ? ` on ${state.host}` : ""}</div></div> : null}
        </div>
      </div>
      <div className="shrink-0 border-t border-border bg-surface px-6 py-3">
        <div className="relative mx-auto max-w-[760px]">
          <Textarea
            ref={inputRef}
            data-advisor-input
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={Math.min(6, Math.max(2, text.split("\n").length))}
            placeholder="Ask about the plan, or say what should change"
            className="min-h-[56px] resize-none bg-surface py-2.5 pr-12"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          />
          <Button variant="primary" size="icon-sm" className="absolute bottom-2 right-2 rounded-full" onClick={send} disabled={sending || !text.trim()} aria-label="send">
            {sending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowUp className="h-3.5 w-3.5" />}
          </Button>
        </div>
        <div className="mx-auto mt-1.5 flex max-w-[760px] items-center gap-3 text-meta">
          <span>enter sends · the advisor only reads; "pause" or "change course" makes it brief the lead</span>
          <span className="flex-1" />
          {state?.has_memory ? <button type="button" className="hover:text-fg hover:underline" onClick={async () => { await api.advisorForget(project); await reload(); }}>start over</button> : null}
        </div>
      </div>
    </div>
  );
}

/** The plan on file, as the lead or the advisor last saved it. */
export function PlanPanel({ plan }: { plan: AdvisorState["plan"] }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="grid gap-1.5" data-plan>
      <button type="button" onClick={() => setOpen((v) => !v)} className="text-label flex items-center gap-2 text-left">
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} Plan
        {plan ? <span className="num text-meta">by {plan.updated_by || "?"} · {age(plan.updated_at)} ago</span> : null}
      </button>
      {open ? (plan ? <Markdown text={plan.text} className="text-[12.5px]" /> : <div className="rounded-md border border-dashed border-border px-2.5 py-2 text-[12px] text-dim">No plan saved yet. The lead saves one with hm_plan after planning; the advisor keeps it current.</div>) : null}
    </div>
  );
}
