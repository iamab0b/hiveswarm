import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import type { Task } from "@/lib/types";
import { short } from "@/lib/utils";

function fail(e: unknown) {
  const msg = e instanceof ApiError ? e.message : String(e);
  toast.error(msg);
}

export const act = {
  approve: async (id: string) => {
    try {
      await api.sessionAnswer(id, "approve");
      toast.success(`Approved ${short(id)}`);
    } catch (e) {
      fail(e);
    }
  },
  deny: async (id: string, message?: string) => {
    try {
      if (message && message.trim()) await api.sessionAnswer(id, "text", message.trim());
      else await api.sessionAnswer(id, "deny");
      toast(`Denied ${short(id)} — it will look for another way or ask you`);
    } catch (e) {
      fail(e);
    }
  },
  option: async (id: string, n: number, label?: string) => {
    try {
      await api.sessionAnswer(id, String(n));
      toast.success(`Answered ${short(id)}${label ? `: ${label}` : ""}`);
    } catch (e) {
      fail(e);
    }
  },
  answerText: async (id: string, text: string) => {
    try {
      await api.sessionAnswer(id, "text", text);
      toast.success(`Answered ${short(id)}`);
    } catch (e) {
      fail(e);
    }
  },
  send: async (id: string, text: string) => {
    try {
      await api.sessionSend(id, text);
      toast.success(`Sent to ${short(id)}`);
    } catch (e) {
      fail(e);
    }
  },
  finish: async (id: string) => {
    try {
      await api.sessionFinish(id);
      toast(`Finishing ${short(id)} — commit, push, verify`);
    } catch (e) {
      fail(e);
    }
  },
  continueOn: async (id: string, agent: string, permission_mode?: string) => {
    try {
      const r = await api.sessionContinue(id, agent, permission_mode);
      toast(r.pending ? `Saving ${short(id)}'s work, then ${agent} takes over` : `Continued as ${short(r.id || "")} on ${agent}`);
    } catch (e) {
      fail(e);
    }
  },
  cancel: async (t: Task) => {
    try {
      const r = await api.cancel(t.id);
      if (r.ok) toast(`Cancelled ${short(t.id)}`);
      else toast.warning(r.reason || "not cancelled");
    } catch (e) {
      fail(e);
    }
  },
  retry: async (id: string, spec?: string | null, acceptance?: string | null) => {
    try {
      const r = await api.retry(id, spec, acceptance);
      if (r.ok) toast.success(`Reopened ${short(id)}`);
      else toast.warning(r.reason || "retry refused");
    } catch (e) {
      fail(e);
    }
  },
  merge: async (id: string, acknowledgeUntested = false) => {
    try {
      const r = await api.merge(id, acknowledgeUntested);
      if (r.ok) toast.success(`Merged ${short(id)} into ${r.into}`);
      else if (r.untested) toast.warning(`${short(id)} is untested: review the diff, then "merge anyway", or retry asking for a test`);
      else toast.error(r.error || r.reason || "merge failed");
      return r.ok;
    } catch (e) {
      fail(e);
      return false;
    }
  },
  resolveDeferred: async (id: string, note?: string) => {
    try {
      await api.resolveDeferred(id, note);
      toast("Resolved");
    } catch (e) {
      fail(e);
    }
  },
  remove: async (id: string) => {
    try {
      await api.remove(id);
      toast(`Deleted ${short(id)}`);
    } catch (e) {
      fail(e);
    }
  },
  clearFlag: async (id: string, kind = "directive") => {
    try {
      await api.clearFlags(id, kind);
      toast(`Cleared the ${kind} flag on ${short(id)}`);
    } catch (e) {
      fail(e);
    }
  },
  addDirective: async (p: { text: string; task_id?: string | null; project?: string | null; every_tools?: number; every_minutes?: number; check?: boolean }) => {
    try {
      const r = await api.addDirective(p);
      toast.success(p.task_id ? `Standing order set for ${short(p.task_id)}` : `Standing order set for every agent in ${p.project}`);
      return r.id;
    } catch (e) {
      fail(e);
      return null;
    }
  },
  removeDirective: async (id: string) => {
    try {
      await api.removeDirective(id);
      toast(`Standing order cleared`);
    } catch (e) {
      fail(e);
    }
  },
};
