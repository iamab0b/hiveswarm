import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { AdapterInfo, Agent, Profile, ProfilesInfo } from "@/lib/types";
import { agentLabel, cn } from "@/lib/utils";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { Input, Select, Switch } from "./ui/fields";

const TH = "text-label px-3 py-2 text-left font-medium";
/** Radix Select shows its placeholder for an empty value, so "default" (no key in worker.toml) needs a value of its own. */
const DEFAULT = "__default__";
const CUSTOM = "__custom__";

function effortOptions(adapter: AdapterInfo | undefined) {
  const opts = [{ value: DEFAULT, label: "default" }];
  for (const e of adapter?.efforts || []) opts.push({ value: e, label: e });
  return opts;
}

/** The model column: the adapter's published list (or what its CLI lists) as a dropdown, with "custom…" for anything else;
 *  a plain text field when the adapter has no list. Saves "" for the adapter's default. While the field has focus a
 *  reload never overwrites what is being typed. */
export function ModelPicker({ adapter, value, onChange, className }: { adapter: AdapterInfo | undefined; value: string; onChange: (v: string) => void; className?: string }) {
  const models = useMemo(() => adapter?.models || [], [adapter]);
  const [custom, setCustom] = useState<"menu" | "value" | false>(!!value && !models.includes(value) ? "value" : false);
  const [text, setText] = useState(value);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (input.current && document.activeElement === input.current) return;
    setText(value);
    setCustom((c) => (!!value && !models.includes(value) ? "value" : c === "menu" ? c : false));
  }, [value, models]);
  const commit = () => {
    const v = text.trim();
    if (v !== value) onChange(v);
    if (!v && models.length) setCustom(false);
  };
  if (!models.length || custom) {
    return (
      <span className="inline-flex items-center gap-1" data-model>
        <Input ref={input} value={text} onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
          placeholder={models.length ? "model id, empty = back to the list" : "default"} className={cn("h-7 w-36 text-[12px]", className)} autoFocus={custom === "menu"} />
      </span>
    );
  }
  const opts = [{ value: DEFAULT, label: "default" }, ...models.map((m) => ({ value: m, label: m })), { value: CUSTOM, label: "custom…" }];
  return (
    <span data-model>
      <Select value={value || DEFAULT} placeholder="default" options={opts} className={cn("h-7 w-36 text-[12px]", className)}
        onChange={(v) => { if (v === CUSTOM) { setCustom("menu"); setText(""); } else onChange(v === DEFAULT ? "" : v); }} />
    </span>
  );
}

function Row({ p, adapters, live, onChange, onRemove }: { p: Profile; adapters: AdapterInfo[]; live: Agent | undefined; onChange: (patch: Partial<Profile>) => Promise<void>; onRemove: () => Promise<void> }) {
  const [lanes, setLanes] = useState(String(p.concurrency));
  const [busy, setBusy] = useState(false);
  useEffect(() => { setLanes(String(p.concurrency)); }, [p.concurrency]);
  const adapter = adapters.find((a) => a.name === p.adapter);
  const save = async (patch: Partial<Profile>) => { setBusy(true); try { await onChange(patch); } finally { setBusy(false); } };
  const overridden = live && live.desired_capacity != null && live.desired_capacity !== p.concurrency;
  return (
    <tr className={cn("border-b border-border last:border-0", !p.enabled && "opacity-60")} data-profile={p.name}>
      <td className="px-3 py-1.5"><span className="font-medium text-fg">{p.name}</span>{!p.installed ? <span className="ml-2 text-meta text-danger">{p.binary} not on PATH</span> : null}</td>
      <td className="whitespace-nowrap px-3 py-1.5 text-muted">{agentLabel(p.adapter)}</td>
      <td className="px-3 py-1.5"><ModelPicker adapter={adapter} value={p.model || ""} onChange={(v) => save({ model: v || null })} /></td>
      <td className="px-3 py-1.5">
        {adapter?.effort_supported ? (
          <Select value={p.effort || DEFAULT} placeholder="default" onChange={(v) => save({ effort: v === DEFAULT ? null : v })} options={effortOptions(adapter)} className="h-7 w-28 text-[12px]" />
        ) : <span className="text-meta">n/a</span>}
      </td>
      <td className="px-3 py-1.5">
        <Input type="number" min={0} max={32} value={lanes} onChange={(e) => setLanes(e.target.value)} onBlur={() => { const n = Math.max(0, Math.min(32, Number(lanes) || 0)); if (n !== p.concurrency) save({ concurrency: n }); }} className="h-7 w-16 text-[12px]" />
        {overridden ? <div className="mt-0.5 whitespace-nowrap text-meta">running {live!.desired_capacity} (hm agents --set)</div> : null}
      </td>
      <td className="px-3 py-1.5"><Switch checked={p.enabled} onChange={(v) => save({ enabled: v })} /></td>
      <td className="px-3 py-1.5 text-right"><Button size="xs" variant="ghost" className="hover:text-danger" disabled={busy} onClick={onRemove}>remove</Button></td>
    </tr>
  );
}

/** A lane the hub runs itself: shown for completeness, configured in the hub's config.toml, nothing to edit here. */
function HubRow({ a }: { a: Agent }) {
  return (
    <tr className="border-b border-border last:border-0" data-profile={a.agent_id} data-local>
      <td className="px-3 py-1.5"><span className="font-medium text-fg">{a.agent_id}</span><span className="ml-2 text-meta">hub lane</span></td>
      <td className="whitespace-nowrap px-3 py-1.5 text-muted">{agentLabel(a.provider || a.agent_id)}</td>
      <td className="px-3 py-1.5"><span className="mono text-[12px] text-muted">{a.model || "default"}</span></td>
      <td className="px-3 py-1.5 text-meta">n/a</td>
      <td className="px-3 py-1.5 num text-muted">1</td>
      <td className="px-3 py-1.5"><span className={cn("text-[12px]", a.alive ? "text-muted" : "text-danger")}>{a.alive ? "up" : "down"}</span></td>
      <td className="px-3 py-1.5 text-right text-meta">{a.where || `[workers.${a.agent_id}] in config.toml on ${a.host}`}</td>
    </tr>
  );
}

/** The `[agents.*]` tables of this machine's worker.toml, editable (the worker applies a change within ~10 s), plus
 *  read-only rows for the lanes the hub runs itself. `info` and `reload` come from the Agents page, which shares them
 *  with the agent cards so both views show the same lane count. */
export function Profiles({ info, reload }: { info: ProfilesInfo | null; reload: () => Promise<void> }) {
  const agents = useStore((s) => s.agents);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ name: "", adapter: "claude_code", model: "", effort: "", concurrency: 1 });
  if (!info) return null;
  const hub = agents.filter((a) => a.local);
  if (!info.ok && !hub.length) return <div className="mt-8 text-[12px] text-dim">Profiles are edited on the machine that runs the worker ({info.reason || "no worker.toml here"}).</div>;
  const adapters = info.adapters.filter((a) => a.installed);
  const change = async (name: string, patch: Partial<Profile>) => {
    const r = await api.setProfile({ name, ...patch });
    if (!r.ok) toast.error(r.reason || "could not save");
    else toast(r.note || "saved");
    await reload();
  };
  const remove = async (name: string) => {
    const r = await api.deleteProfile(name);
    if (!r.ok) toast.error(r.reason || "could not remove");
    await reload();
  };
  const add = async () => {
    const r = await api.setProfile({ name: draft.name.trim(), adapter: draft.adapter, model: draft.model || null, effort: draft.effort || null, concurrency: draft.concurrency });
    if (!r.ok) { toast.error(r.reason || "could not add"); return; }
    toast(r.note || "added");
    setAdding(false);
    setDraft({ name: "", adapter: draft.adapter, model: "", effort: "", concurrency: 1 });
    await reload();
  };
  const draftAdapter = info.adapters.find((a) => a.name === draft.adapter);
  return (
    <section className="mt-8" data-profiles>
      <div className="mb-2 flex items-end justify-between gap-3">
        <div>
          <div className="text-label text-fg">Profiles{info.ok ? <> on {info.host}</> : null}</div>
          <div className="text-[12px] text-muted">
            {info.ok ? <>Each row is an <span className="mono">[agents.*]</span> table in <span className="mono">{info.path}</span>: the same CLI with its own model, effort and lanes. Changes apply within about ten seconds; profiles behind one adapter share its sign-in and rate limits.</>
              : <>Profiles are edited on the machine that runs the worker ({info.reason || "no worker.toml here"}).</>}
            {hub.length ? <> Hub lanes are configured in the hub's <span className="mono">config.toml</span> and cannot be changed from here.</> : null}
          </div>
        </div>
        {info.ok ? <Button size="sm" variant="secondary" onClick={() => setAdding((v) => !v)}>{adding ? "close" : "add profile"}</Button> : null}
      </div>
      <Card className="overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-border">
              <th className={TH}>profile</th><th className={TH}>adapter</th><th className={TH}>model</th><th className={TH}>effort</th><th className={TH}>lanes</th><th className={TH}>on</th><th className={TH} />
            </tr>
          </thead>
          <tbody>
            {info.profiles.map((p) => <Row key={p.name} p={p} adapters={info.adapters} live={agents.find((a) => a.agent_id === p.name && a.host === info.host)} onChange={(patch) => change(p.name, patch)} onRemove={() => remove(p.name)} />)}
            {hub.map((a) => <HubRow key={a.agent_id} a={a} />)}
            {adding ? (
              <tr className="bg-surface-2" data-profile-draft>
                <td className="px-3 py-1.5"><Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="opus_max" className="h-7 w-32 text-[12px]" autoFocus /></td>
                <td className="px-3 py-1.5"><Select value={draft.adapter} onChange={(v) => setDraft({ ...draft, adapter: v, effort: "", model: "" })} options={adapters.map((a) => ({ value: a.name, label: agentLabel(a.name) }))} className="h-7 w-36 text-[12px]" /></td>
                <td className="px-3 py-1.5"><ModelPicker key={draft.adapter} adapter={draftAdapter} value={draft.model} onChange={(v) => setDraft({ ...draft, model: v })} /></td>
                <td className="px-3 py-1.5">{draftAdapter?.effort_supported ? <Select value={draft.effort || DEFAULT} placeholder="default" onChange={(v) => setDraft({ ...draft, effort: v === DEFAULT ? "" : v })} options={effortOptions(draftAdapter)} className="h-7 w-28 text-[12px]" /> : <span className="text-meta">n/a</span>}</td>
                <td className="px-3 py-1.5"><Input type="number" min={0} max={32} value={draft.concurrency} onChange={(e) => setDraft({ ...draft, concurrency: Number(e.target.value) || 1 })} className="h-7 w-16 text-[12px]" /></td>
                <td className="px-3 py-1.5" />
                <td className="px-3 py-1.5 text-right"><Button size="xs" variant="primary" disabled={!/^[A-Za-z0-9_-]{1,40}$/.test(draft.name)} onClick={add}>add</Button></td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </Card>
    </section>
  );
}
