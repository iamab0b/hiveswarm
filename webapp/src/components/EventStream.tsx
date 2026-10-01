import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDownToLine, Pause } from "lucide-react";
import { Link } from "react-router-dom";
import { agentColor, agentLabel, classifyEntry, clock, cn, type FeedKind } from "@/lib/utils";
import type { FeedEntry } from "@/lib/types";
import { Button } from "./ui/button";

const KIND: Record<string, { glyph: string; cls: string; body: string }> = {
  msg: { glyph: "»", cls: "text-muted", body: "text-fg" },
  tool: { glyph: "▸", cls: "text-muted", body: "text-muted" },
  result: { glyph: "└", cls: "text-dim", body: "text-dim" },
  err: { glyph: "!", cls: "text-danger", body: "text-muted" },
  info: { glyph: "•", cls: "text-dim", body: "text-muted" },
  status: { glyph: "◆", cls: "text-muted", body: "text-muted" },
  think: { glyph: "…", cls: "text-dim", body: "text-dim italic" },
  out: { glyph: "", cls: "", body: "text-muted" },
  you: { glyph: "›", cls: "text-accent", body: "text-fg font-medium" },
  daemon: { glyph: "◆", cls: "text-dim", body: "text-muted" },
  handoff: { glyph: "⇢", cls: "text-muted", body: "text-muted" },
  good: { glyph: "◆", cls: "text-success", body: "text-fg" },
  bad: { glyph: "◆", cls: "text-danger", body: "text-fg" },
  note: { glyph: "◆", cls: "text-dim", body: "text-muted" },
  ask: { glyph: "?", cls: "text-accent", body: "text-fg" },
  warn: { glyph: "!", cls: "text-accent", body: "text-muted" },
  verifier: { glyph: "│", cls: "text-dim", body: "text-muted" },
};

function clip(text: string, maxLines: number) {
  const lines = text.split("\n");
  if (lines.length <= maxLines) return text;
  return lines.slice(0, maxLines).join("\n") + `\n… (+${lines.length - maxLines} lines)`;
}

export function EventLine({ e, showTask, showAgent, dense }: { e: FeedEntry; showTask?: boolean; showAgent?: boolean; dense?: boolean }) {
  const { agent, kind } = classifyEntry(e.source, e.chunk);
  const k = KIND[kind as FeedKind] || KIND.out;
  const maxLines = dense ? (kind === "result" || kind === "out" ? 3 : 6) : kind === "handoff" ? 10 : kind === "you" ? (showTask ? 2 : 6) : 14;
  const body = clip(e.chunk.replace(/\s+$/, ""), maxLines);
  return (
    <div className={cn("group grid items-start gap-x-2 px-2 hover:bg-surface-2/60", dense ? "py-[2px]" : "py-[3px]")}
      style={{ gridTemplateColumns: `62px ${showTask ? "60px " : ""}${showAgent ? "96px " : ""}14px minmax(0,1fr)` }}>
      <span className="mono num pt-[1px] text-[11px] text-dim">{clock(e.ts)}</span>
      {showTask ? (
        <Link to={`/t/${e.task_id}`} className="mono pt-[1px] text-[11px] text-dim hover:text-fg">{e.task_id.slice(0, 8)}</Link>
      ) : null}
      {showAgent ? (
        <span className="flex items-center gap-1.5 truncate pt-[1px] text-[11px] text-muted">
          {agent ? <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: agentColor(agent) }} /> : null}
          {agent ? agentLabel(agent) : "hive"}
        </span>
      ) : null}
      <span className={cn("mono text-[12px] leading-[18px]", k.cls)}>{k.glyph}</span>
      <span className={cn("mono whitespace-pre-wrap break-words text-[12px] leading-[18px]", k.body)}>{body}</span>
    </div>
  );
}

export function EventStream({ entries, showTask, showAgent, className, emptyText = "nothing yet", dense }: {
  entries: FeedEntry[];
  showTask?: boolean;
  showAgent?: boolean;
  className?: string;
  emptyText?: string;
  dense?: boolean;
}) {
  const parentRef = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);
  const count = entries.length;
  const virt = useVirtualizer({
    count,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 24,
    overscan: 20,
    getItemKey: (i) => entries[i].id,
  });
  const lastId = count ? entries[count - 1].id : 0;
  useLayoutEffect(() => {
    if (follow && count) virt.scrollToIndex(count - 1, { align: "end" });
  }, [lastId, count, follow, virt]);
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    const onScroll = () => {
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      setFollow(atBottom);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);
  const items = virt.getVirtualItems();
  const empty = useMemo(() => count === 0, [count]);
  return (
    <div className={cn("relative h-full", className)}>
      <div ref={parentRef} className="h-full overflow-y-auto">
        {empty ? (
          <div className="p-6 text-center text-[12px] text-dim">{emptyText}</div>
        ) : (
          <div style={{ height: virt.getTotalSize(), position: "relative" }}>
            {items.map((vi) => (
              <div
                key={vi.key}
                ref={virt.measureElement}
                data-index={vi.index}
                style={{ position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${vi.start}px)` }}
              >
                <EventLine e={entries[vi.index]} showTask={showTask} showAgent={showAgent} dense={dense} />
              </div>
            ))}
          </div>
        )}
      </div>
      {!follow && count > 0 ? (
        <div className="absolute bottom-3 right-4">
          <Button size="sm" variant="secondary" className="shadow-[var(--shadow-pop)]" onClick={() => { setFollow(true); virt.scrollToIndex(count - 1, { align: "end" }); }}>
            <ArrowDownToLine className="h-3.5 w-3.5" /> follow
          </Button>
        </div>
      ) : null}
      {follow && count > 0 ? (
        <div className="pointer-events-none absolute bottom-2 right-3 flex items-center gap-1 text-meta"><Pause className="h-3 w-3" />scroll up to pause</div>
      ) : null}
    </div>
  );
}
