import type { AdvisorState, Agent, AgentTable, DeferredItem, Directive, FeedEntry, LiveStep, InboxItem, LocalInfo, LogEntry, Plan, ProfilesInfo, Project, ProjectSettings, RulesetRow, SessionInfo, StatRow, Summary, Task, TaskDetail } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(method: string, path: string, body?: unknown, params?: Record<string, unknown>): Promise<T> {
  const url = new URL("/api" + path, window.location.origin);
  if (params) for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
  const r = await fetch(url.toString(), {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try {
      const j = await r.json();
      msg = j.detail || j.error || JSON.stringify(j);
    } catch {
      msg = await r.text();
    }
    throw new ApiError(r.status, String(msg).slice(0, 300));
  }
  const text = await r.text();
  return (text ? JSON.parse(text) : null) as T;
}

export const api = {
  local: () => req<LocalInfo>("GET", "/local"),
  summary: () => req<Summary>("GET", "/summary"),
  projects: () => req<Record<string, Project>>("GET", "/projects"),
  createProject: (name: string) => req<{ name: string; repo_path: string }>("POST", "/projects", { name }),
  deleteProject: (name: string, purge: boolean) => req<{ ok: boolean; tasks_removed: number; repo_removed: boolean; repo_path?: string }>("DELETE", `/projects/${name}`, undefined, { purge }),
  openLocal: (project: string) => req<{ ok: boolean; reason?: string; path?: string; win_path?: string }>("POST", "/local/open", { project }),
  syncLocal: (project: string) => req<{ ok: boolean; note?: string; reason?: string; path?: string }>("POST", "/local/sync", { project }),
  tasks: (limit = 300) => req<Task[]>("GET", "/tasks", undefined, { limit }),
  task: (id: string) => req<TaskDetail>("GET", `/tasks/${id}`),
  log: (id: string, since = 0) => req<{ entries: LogEntry[]; last_seq: number }>("GET", `/tasks/${id}/log`, undefined, { since }),
  diff: (id: string) => req<{ ok: boolean; branch?: string; stat?: string; diff?: string; error?: string }>("GET", `/tasks/${id}/diff`),
  feed: (p: { since?: number; tail?: number }) => req<{ entries: FeedEntry[]; last_id: number }>("GET", "/feed", undefined, p),
  inbox: () => req<{ items: InboxItem[]; count: number }>("GET", "/inbox"),
  agents: () => req<Agent[]>("GET", "/agents"),
  setCapacity: (agent: string, capacity: number | null) =>
    req<{ ok: boolean; capacity: number; desired_capacity: number | null }>("POST", `/agents/${agent}/capacity`, { capacity }),
  stats: () => req<StatRow[]>("GET", "/stats"),
  agentStats: () => req<AgentTable>("GET", "/stats/agents"),
  live: (id: string) => req<LiveStep>("GET", `/tasks/${id}/live`),
  addTask: (project: string, spec: string, acceptance: string | null) =>
    req<{ id: string }>("POST", "/tasks", { project, spec, acceptance, origin: "app" }),
  newSession: (p: { project: string; spec: string; acceptance?: string | null; agent: string; permission_mode: string; count?: number; lead?: boolean }) =>
    req<{ ids: string[]; id: string }>("POST", "/sessions", { ...p, origin: "app" }),
  sessionAnswer: (id: string, choice: string, text?: string) => req<{ ok: boolean }>("POST", `/sessions/${id}/answer`, { choice, text }),
  sessionSend: (id: string, text: string) => req<{ ok: boolean }>("POST", `/sessions/${id}/send`, { text }),
  sessionFinish: (id: string) => req<{ ok: boolean }>("POST", `/sessions/${id}/finish`),
  sessionContinue: (id: string, agent: string, permission_mode?: string) => req<{ ok: boolean; id?: string; pending?: boolean }>("POST", `/sessions/${id}/continue`, { agent, permission_mode }),
  sessionViewed: (id: string) => req<{ ok: boolean }>("POST", `/sessions/${id}/viewed`),
  cancel: (id: string) => req<{ ok: boolean; reason?: string }>("POST", `/tasks/${id}/cancel`),
  retry: (id: string, spec?: string | null, acceptance?: string | null) => req<{ ok: boolean; reason?: string }>("POST", `/tasks/${id}/retry`, { spec, acceptance }),
  merge: (id: string, acknowledgeUntested = false) =>
    req<{ ok: boolean; into?: string; error?: string; reason?: string; output?: string; untested?: boolean }>("POST", `/tasks/${id}/merge`, undefined, acknowledgeUntested ? { acknowledge_untested: "true" } : undefined),
  deferred: (project: string, includeResolved = false) =>
    req<{ project: string; open: number; items: DeferredItem[] }>("GET", `/projects/${project}/deferred`, undefined, includeResolved ? { include_resolved: "true" } : undefined),
  resolveDeferred: (id: string, note?: string) => req<{ ok: boolean; item: DeferredItem }>("POST", `/deferred/${id}/resolve`, { by: "you", note: note || null }),
  rulesetStats: () => req<{ rows: RulesetRow[] }>("GET", "/stats/rulesets"),
  profiles: () => req<ProfilesInfo>("GET", "/local/profiles"),
  setProfile: (p: { name: string; adapter?: string; model?: string | null; effort?: string | null; concurrency?: number; enabled?: boolean }) =>
    req<{ ok: boolean; reason?: string; note?: string }>("POST", "/local/profiles", p),
  deleteProfile: (name: string) => req<{ ok: boolean; reason?: string }>("DELETE", `/local/profiles/${name}`),
  advisor: (project: string) => req<AdvisorState>("GET", `/advisor/${project}`),
  advisorTalk: (project: string, text: string) => req<{ ok: boolean; turn: string }>("POST", `/advisor/${project}/talk`, { text }),
  advisorForget: (project: string) => req<{ ok: boolean }>("DELETE", `/advisor/${project}`),
  projectPlan: (project: string) => req<{ project: string; plan: Plan | null }>("GET", `/projects/${project}/plan`),
  setPlan: (project: string, text: string) => req<{ project: string; plan: Plan }>("PUT", `/projects/${project}/plan`, { text, by: "you" }),
  pause: (project: string, reason: string) => req<{ ok: boolean }>("POST", `/projects/${project}/pause`, { by: "you", reason }),
  resume: (project: string) => req<{ ok: boolean; was_paused: boolean }>("POST", `/projects/${project}/resume`),
  projectSettings: (name: string) => req<ProjectSettings>("GET", `/projects/${name}/settings`),
  setProjectSettings: (name: string, body: { agents?: string[]; ruleset?: string; ruleset_intensity?: string }) =>
    req<ProjectSettings>("POST", `/projects/${name}/settings`, body),
  remove: (id: string) => req<{ ok: boolean }>("DELETE", `/tasks/${id}`),
  plan: (p: { goal: string; project: string; new_repo: boolean }) =>
    req<{ tasks: { spec: string; acceptance: string | null }[]; error?: string | null }>("POST", "/plan", p),
  directives: (p: { project?: string; task_id?: string; for_task?: string; include_inactive?: boolean }) => req<Directive[]>("GET", "/directives", undefined, p),
  addDirective: (p: { text: string; task_id?: string | null; project?: string | null; every_tools?: number; every_minutes?: number; check?: boolean }) =>
    req<{ id: string }>("POST", "/directives", { ...p, created_by: "you" }),
  removeDirective: (id: string) => req<{ ok: boolean }>("DELETE", `/directives/${id}`),
  checkDirective: (id: string, task_id: string) => req<{ checked: boolean; violated?: boolean; p?: number; reason?: string }>("POST", `/directives/${id}/check`, { task_id }),
  clearFlags: (id: string, kind?: string) => req<{ ok: boolean }>("POST", `/tasks/${id}/flags/clear`, undefined, { kind }),
};

export interface LeadRow { project: string; lead: (Task & { session: SessionInfo }) | null }
export const leadApi = {
  list: () => req<LeadRow[]>("GET", "/leads"),
  talk: (project: string, message?: string, permission_mode = "auto") =>
    req<{ id: string; created: boolean; reopened?: boolean; state: string }>("POST", "/leads", { project, message, permission_mode }),
};
