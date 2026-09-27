import { create } from "zustand";
import type { Agent, FeedEntry, InboxItem, LocalInfo, Project, Summary, Task } from "./types";
import { api } from "./api";
import { firstLine, isTerminal, liveAttention, sessionOf } from "./utils";

const FEED_MAX = 6000;
const PER_TASK_MAX = 2500;

export interface Notice {
  id: string;
  kind: string;
  title: string;
  body: string;
  taskId: string;
  ts: number;
}

interface State {
  connected: boolean;
  error: string | null;
  summary: Summary | null;
  tasks: Task[];
  taskById: Record<string, Task>;
  inbox: InboxItem[];
  agents: Agent[];
  projects: Record<string, Project>;
  local: LocalInfo | null;
  feed: FeedEntry[];
  byTask: Record<string, FeedEntry[]>;
  feedVersion: number;
  lastFeedId: number;
  notices: Notice[];
  theme: "dark" | "light";
  seenAttention: Set<string>;
  bootstrapped: boolean;

  start: () => void;
  loadProjects: () => Promise<void>;
  setTheme: (t: "dark" | "light") => void;
  dismissNotice: (id: string) => void;
}

let source: EventSource | null = null;

function playChime() {
  try {
    const ctx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = "sine";
    o.frequency.setValueAtTime(880, ctx.currentTime);
    o.frequency.exponentialRampToValueAtTime(1320, ctx.currentTime + 0.12);
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.08, ctx.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.35);
    o.connect(g).connect(ctx.destination);
    o.start();
    o.stop(ctx.currentTime + 0.4);
  } catch {
    /* no audio */
  }
}

export const useStore = create<State>((set, get) => ({
  connected: false,
  error: null,
  summary: null,
  tasks: [],
  taskById: {},
  inbox: [],
  agents: [],
  projects: {},
  local: null,
  feed: [],
  byTask: {},
  feedVersion: 0,
  lastFeedId: 0,
  notices: [],
  theme: (localStorage.getItem("hm.theme") as "dark" | "light") || "dark",
  seenAttention: new Set(),
  bootstrapped: false,

  setTheme: (t) => {
    localStorage.setItem("hm.theme", t);
    document.documentElement.classList.toggle("dark", t === "dark");
    set({ theme: t });
  },

  dismissNotice: (id) => set((s) => ({ notices: s.notices.filter((n) => n.id !== id) })),

  loadProjects: async () => {
    try {
      const projects = await api.projects();
      set({ projects });
    } catch {
      /* keep old */
    }
  },

  start: () => {
    if (source) return;
    document.documentElement.classList.toggle("dark", get().theme === "dark");
    api.local().then((local) => set({ local })).catch(() => undefined);
    get().loadProjects();
    api.agents().then((agents) => set({ agents })).catch(() => undefined);

    const connect = () => {
      source = new EventSource("/api/events");
      source.onopen = () => set({ connected: true, error: null });
      source.onerror = () => set({ connected: false });
      source.addEventListener("error", () => undefined);
      source.addEventListener("state", (ev) => {
        const d = JSON.parse((ev as MessageEvent).data) as { summary: Summary; tasks: Task[]; inbox: InboxItem[]; agents: Agent[] };
        const prev = get();
        const taskById: Record<string, Task> = {};
        for (const t of d.tasks) taskById[t.id] = t;
        const notices = [...prev.notices];
        const seen = prev.seenAttention;
        if (prev.bootstrapped) {
          for (const t of d.tasks) {
            const was = prev.taskById[t.id];
            if (was && was.state !== t.state && isTerminal(t.state)) {
              notices.push({
                id: `${t.id}-${t.state}-${t.updated_at}`,
                kind: t.state,
                title: t.state === "done" ? "Done" : t.state === "failed" ? "Failed" : "Cancelled",
                body: `${t.id.slice(0, 8)} · ${firstLine(t.spec, 60)}`,
                taskId: t.id,
                ts: Date.now(),
              });
            }
          }
          for (const it of d.inbox) {
            const key = `${it.id}:${it.attention.kind}:${it.attention.since || 0}`;
            if (seen.has(key)) continue;
            seen.add(key);
            if (["question", "permission", "usage_limit"].includes(it.attention.kind)) {
              notices.push({
                id: key,
                kind: it.attention.kind,
                title: it.attention.kind === "permission" ? "Needs approval" : it.attention.kind === "question" ? "Asks you" : "Usage limit",
                body: `${it.agent || ""} ${it.id.slice(0, 8)} · ${firstLine(it.attention.summary, 80)}`,
                taskId: it.id,
                ts: Date.now(),
              });
              playChime();
              if ("Notification" in window && Notification.permission === "granted") {
                try {
                  new Notification(`Hiveswarm: ${it.agent || "agent"} ${it.attention.kind === "permission" ? "needs approval" : "asks you"}`, {
                    body: firstLine(it.attention.summary, 100),
                    tag: key,
                  });
                } catch {
                  /* ignore */
                }
              }
            }
          }
        } else {
          for (const it of d.inbox) seen.add(`${it.id}:${it.attention.kind}:${it.attention.since || 0}`);
        }
        const inboxCount = d.inbox.length;
        document.title = inboxCount ? `(${inboxCount}) Hiveswarm` : "Hiveswarm";
        set({
          summary: d.summary,
          tasks: d.tasks,
          taskById,
          inbox: d.inbox,
          agents: d.agents,
          notices: notices.slice(-30),
          seenAttention: seen,
          bootstrapped: true,
          connected: true,
          error: null,
        });
      });
      source.addEventListener("feed", (ev) => {
        const entries = JSON.parse((ev as MessageEvent).data) as FeedEntry[];
        if (!entries.length) return;
        const s = get();
        let last = s.lastFeedId;
        const fresh = entries.filter((e) => e.id > last);
        if (!fresh.length) return;
        const feed = s.feed.concat(fresh);
        const byTask = { ...s.byTask };
        for (const e of fresh) {
          const arr = byTask[e.task_id] ? byTask[e.task_id].concat(e) : [e];
          byTask[e.task_id] = arr.length > PER_TASK_MAX ? arr.slice(-PER_TASK_MAX) : arr;
          if (e.id > last) last = e.id;
        }
        set({
          feed: feed.length > FEED_MAX ? feed.slice(-FEED_MAX) : feed,
          byTask,
          lastFeedId: last,
          feedVersion: s.feedVersion + 1,
        });
      });
      source.addEventListener("error", (ev) => {
        const me = ev as MessageEvent;
        if (me.data) {
          try {
            set({ error: JSON.parse(me.data).error });
          } catch {
            /* ignore */
          }
        }
      });
    };
    connect();
  },
}));

export function useTask(id: string | undefined): Task | undefined {
  return useStore((s) => (id ? s.taskById[id] : undefined));
}

export function useAttention(id: string | undefined) {
  return useStore((s) => {
    const t = id ? s.taskById[id] : undefined;
    return t ? liveAttention(t) : null;
  });
}

export function useSession(id: string | undefined) {
  return useStore((s) => {
    const t = id ? s.taskById[id] : undefined;
    return t ? sessionOf(t) : {};
  });
}
