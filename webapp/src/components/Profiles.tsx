import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { AdapterInfo, Profile, ProfilesInfo } from "@/lib/types";
import { agentLabel, cn } from "@/lib/utils";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { Input, Select, Switch } from "./ui/fields";

const TH = "text-label px-3 py-2 text-left font-medium";

function effortOptions(adapter: AdapterInfo | undefined) {
  const opts = [{ value: "", label: "default" }];
  for (const e of adapter?.efforts || []) opts.push({ value: e, label: e });
  return opts;
}

function Row({ p, adapters, onChange, onRemove }: { p: Profile; adapters: AdapterInfo[]; onChange: (patch: Partial<Profile>) => Promise<void>; onRemove: () => Promise<void> }) {
  const [model, setModel] = useState(p.model || "");
  const [lanes, setLanes] = useState(String(p.concurrency));
  const [busy, setBusy] = useState(false);
  useEffect(() => { setModel(p.model || ""); setLanes(String(p.concurrency)); }, [p.model, p.concurrency]);
  const adapter = adapters.find((a) => a.name === p.adapter);
  const save = async (patch: Partial<Profile>) => { setBusy(true); try { await onChange(patch); } finally { setBusy(false); } };
  return (
    <tr className={cn("border-b border-border last:border-0", !p.enabled && "opacity-60")} data-profile={p.name}>
      <td className="px-3 py-1.5"><span className="font-medium text-fg">{p.name}</span>{!p.installed ? <span className="ml-2 text-meta text-danger">{p.binary} not on PATH</span> : null}</td>
      <td className="px-3 py-1.5 text-muted">{agentLabel(p.adapter)}</td>
      <td className="px-3 py-1.5"><Input value={model} onChange={(e) => setModel(e.target.value)} onBlur={() => { if (model !== (p.model || "")) save({ model: model || null }); }} placeholder="default" className="h-7 w-36 text-[12px]" /></td>
      <td className="px-3 py-1.5">
        {adapter?.effort_supported ? (
          <Select value={p.effort || ""} onChange={(v) => save({ effort: v || null })} options={effortOptions(adapter)} className="h-7 w-28 text-[12px]" />
        ) : <span className="text-meta">n/a</span>}
      </td>
      <td className="px-3 py-1.5"><Input type="number" min={0} max={32} value={lanes} onChange={(e) => setLanes(e.target.value)} onBlur={() => { const n = Math.max(0, Math.min(32, Number(lanes) || 0)); if (n !== p.concurrency) save({ concurrency: n }); }} className="h-7 w-16 text-[12px]" /></td>
      <td className="px-3 py-1.5"><Switch checked={p.enabled} onChange={(v) => save({ enabled: v })} /></td>
      <td className="px-3 py-1.5 text-right"><Button size="xs" variant="ghost" className="hover:text-danger" disabled={busy} onClick={onRemove}>remove</Button></td>
    </tr>
  );
}

/** The `[agents.*]` tables of this machine's worker.toml, editable; the worker applies a change within ~10 s. */
export function Profiles() {
  const [info, setInfo] = useState<ProfilesInfo | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ name: "", adapter: "claude_code", model: "", effort: "", concurrency: 1 });
  const reload = useCallback(() => api.profiles().then(setInfo).catch(() => setInfo({ ok: false, reason: "not reachable", profiles: [], adapters: [] })), []);
  useEffect(() => { reload(); }, [reload]);
  if (!info) return null;
  if (!info.ok) return <div className="text-[12px] text-dim">Profiles are edited on the machine that runs the worker ({info.reason || "no worker.toml here"}).</div>;
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
          <div className="text-label text-fg">Profiles on {info.host}</div>
          <div className="text-[12px] text-muted">Each row is an <span className="mono">[agents.*]</span> table in <span className="mono">{info.path}</span>: the same CLI with its own model, effort and lanes. Changes apply within about ten seconds; profiles behind one adapter share its sign-in and rate limits.</div>
        </div>
        <Button size="sm" variant="secondary" onClick={() => setAdding((v) => !v)}>{adding ? "close" : "add profile"}</Button>
      </div>
      <Card className="overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-border">
              <th className={TH}>profile</th><th className={TH}>adapter</th><th className={TH}>model</th><th className={TH}>effort</th><th className={TH}>lanes</th><th className={TH}>on</th><th className={TH} />
            </tr>
          </thead>
          <tbody>
            {info.profiles.map((p) => <Row key={p.name} p={p} adapters={info.adapters} onChange={(patch) => change(p.name, patch)} onRemove={() => remove(p.name)} />)}
            {adding ? (
              <tr className="bg-surface-2" data-profile-draft>
                <td className="px-3 py-1.5"><Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="opus_max" className="h-7 w-32 text-[12px]" autoFocus /></td>
                <td className="px-3 py-1.5"><Select value={draft.adapter} onChange={(v) => setDraft({ ...draft, adapter: v, effort: "" })} options={adapters.map((a) => ({ value: a.name, label: agentLabel(a.name) }))} className="h-7 w-36 text-[12px]" /></td>
                <td className="px-3 py-1.5"><Input value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })} placeholder="opus" className="h-7 w-36 text-[12px]" /></td>
                <td className="px-3 py-1.5">{draftAdapter?.effort_supported ? <Select value={draft.effort} onChange={(v) => setDraft({ ...draft, effort: v })} options={effortOptions(draftAdapter)} className="h-7 w-28 text-[12px]" /> : <span className="text-meta">n/a</span>}</td>
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
