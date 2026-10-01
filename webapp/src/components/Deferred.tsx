import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import type { DeferredItem } from "@/lib/types";
import { age, agentLabel, short } from "@/lib/utils";
import { Button } from "./ui/button";
import { act } from "./actions";

export function useDeferred(project: string | null | undefined, every = 10000) {
  const [items, setItems] = useState<DeferredItem[] | null>(null);
  const reload = useCallback(() => {
    if (!project) return Promise.resolve();
    return api.deferred(project).then((r) => setItems(r.items)).catch(() => undefined);
  }, [project]);
  useEffect(() => {
    let alive = true;
    const go = () => { if (alive) reload(); };
    go();
    const i = setInterval(go, every);
    return () => { alive = false; clearInterval(i); };
  }, [reload, every]);
  return { items: items || [], loaded: items !== null, reload };
}

/** The project's deferred ledger: what agents reported under Deferred in their handoffs and nobody has closed yet. */
export function DeferredList({ project, title = "Deferred" }: { project: string | null | undefined; title?: string }) {
  const { items, reload } = useDeferred(project);
  return (
    <div className="grid gap-1.5" data-deferred-count={items.length}>
      <div className="text-label flex items-center gap-2">
        {title}
        <span className="num text-dim">{items.length || ""}</span>
      </div>
      {items.length ? (
        <div className="grid gap-1">
          {items.map((d) => (
            <div key={d.id} className="group rounded-md border border-border bg-surface py-1.5 pl-3 pr-2">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1 text-[12.5px] leading-[18px] text-fg">{d.text}</div>
                <Button size="xs" variant="ghost" className="opacity-0 group-hover:opacity-100" onClick={async () => { await act.resolveDeferred(d.id); reload(); }}>resolve</Button>
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-meta">
                {d.agent ? <span>{agentLabel(d.agent)}</span> : null}
                {d.task_id ? <Link to={`/t/${d.task_id}`} className="mono hover:text-fg hover:underline">{short(d.task_id)}</Link> : null}
                <span>{age(d.created_at)} ago</span>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-md border border-dashed border-border px-2.5 py-2 text-[12px] text-dim">
          Nothing deferred. Agents report shortcuts and loose ends under Deferred in their handoff and they land here.
        </div>
      )}
    </div>
  );
}
