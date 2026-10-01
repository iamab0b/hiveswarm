export type TaskState =
  | "pending" | "classifying" | "classified" | "assigned" | "claimed" | "running" | "verifying"
  | "done" | "failed" | "blocked" | "abandoned";

export type AttentionKind = "permission" | "question" | "input" | "usage_limit" | "handoff" | "failed" | "directive" | "stalled" | "untested";

export interface Attention {
  kind: AttentionKind;
  summary: string;
  detail?: string;
  options?: string[];
  risk?: string;
  since?: number;
  ref?: string;
}

export interface TaskFlag {
  kind: string;
  summary: string;
  detail?: string;
  ref?: string;
  since?: number;
}

export interface Directive {
  id: string;
  task_id: string | null;
  project: string | null;
  text: string;
  every_tools: number;
  every_minutes: number;
  check: boolean;
  created_by?: string | null;
  created_at: number;
  active: boolean;
  fires: number;
  violations: number;
  last_fired_at?: number | null;
  last_result?: string | null;
}

export interface SessionInfo {
  agent?: string | null;
  permission_mode?: string;
  turn?: "starting" | "working" | "waiting" | "idle" | "exited";
  attention?: Attention | null;
  host?: string | null;
  tmux?: string | null;
  name?: string | null;
  claude_session_id?: string | null;
  transcript_path?: string | null;
  unread?: number;
  turns?: number;
  asks?: number;
  auto_approved?: number;
  lead?: boolean;
  persistent?: boolean;
  started_at?: number | null;
  continued_from?: string;
  continued_in?: string;
}

export interface Task {
  id: string;
  project: string;
  spec: string;
  acceptance: string | null;
  repo_path: string;
  base_ref: string;
  state: TaskState;
  kind: "task" | "session";
  session?: string | SessionInfo | null;
  claimed_by: string | null;
  attempts: number;
  max_attempts: number;
  created_at: number;
  updated_at: number;
  worktree?: string | null;
  lease_expires?: number | null;
  preferred_agent?: string | null;
  flags?: string | TaskFlag[] | null;
}

export interface Attempt {
  id: string;
  task_id: string;
  agent: string;
  model?: string | null;
  started_at: number;
  ended_at?: number | null;
  outcome?: string | null;
  wall_seconds?: number | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  diff_stat?: string | null;
  verifier_log?: string | null;
  branch?: string | null;
  steps?: number | null;
  step_avg_s?: number | null;
  step_p90_s?: number | null;
  step_max_s?: number | null;
  slow_steps?: number | null;
  silence_max_s?: number | null;
  first_action_s?: number | null;
}

export interface Classification {
  task_type: string;
  type_conf: number;
  difficulty: number;
  difficulty_conf: number;
  is_multistep?: number;
  needs_tools?: number;
  source: string;
}

export interface TaskDetail {
  task: Task;
  classification: Classification | null;
  attempts: Attempt[];
}

export interface FeedEntry {
  id: number;
  task_id: string;
  seq: number;
  ts: number;
  source: string;
  chunk: string;
}

export interface LogEntry {
  seq: number;
  ts: number;
  source: string;
  chunk: string;
}

export interface Agent {
  agent_id: string;
  host: string | null;
  capabilities: string[];
  last_seen: number;
  registered_at: number;
  capacity: number;
  desired_capacity?: number | null;
  provider?: string | null;
  alive: boolean;
  busy: number;
}

export interface InboxItem {
  id: string;
  kind: "task" | "session";
  project: string;
  state: TaskState;
  agent: string | null;
  spec: string;
  updated_at: number;
  turn?: string;
  unread?: number;
  attention: Attention;
}

export interface Summary {
  tasks: Record<string, number>;
  attempts_24h: Record<string, number>;
  by_agent_24h?: Record<string, Record<string, number>>;
  dispatcher_alive: boolean;
  agents_alive: string[];
  cache?: { hits?: number; entries?: number };
}

export interface LocalCopy {
  host: string;
  path: string;
  win_path?: string | null;
  head?: string | null;
  synced_at?: number | null;
  dirty: boolean;
  ahead: number;
  behind: number;
  note?: string | null;
}

export interface Project {
  repo_path: string;
  verify_image?: string;
  acceptance_templates?: string[];
  posture?: string;
  head?: string | null;
  branch?: string | null;
  local?: LocalCopy | null;
  ruleset?: string;
  deferred_open?: number;
}

export interface DeferredItem {
  id: string;
  project: string;
  task_id: string | null;
  attempt_id: string | null;
  agent: string | null;
  text: string;
  created_at: number;
  resolved_at: number | null;
  resolved_by: string | null;
  note: string | null;
}

export interface RulesetRow {
  ruleset: string;
  n: number;
  passes: number;
  pass_rate: number | null;
  avg_lines: number | null;
  avg_wall_s: number | null;
  untested: number;
}

export interface LocalInfo {
  hostname: string;
  tmux: boolean;
  tmux_alive: boolean;
  daemon: string;
}

export interface StatRow {
  agent: string;
  task_type: string;
  diff_band: number;
  n: number;
  pass_rate?: number;
  passes?: number;
  fails?: number;
  avg_seconds?: number;
  avg_usd?: number;
  [k: string]: unknown;
}

export type AgentStatus = "healthy" | "slow" | "failing" | "unknown";

export interface AgentCell {
  agent: string;
  task_type: string;
  n: number;
  passes: number;
  fails: number;
  pass_rate: number | null;
  pass_estimate: number | null;
  avg_wall_s: number | null;
  avg_step_s: number | null;
  p90_step_s: number | null;
  max_step_s: number | null;
  steps: number;
  slow_steps: number;
  slow_step_rate: number | null;
  silence_max_s: number | null;
  first_action_s: number | null;
  trend: number | null;
  last_at: number;
  status: AgentStatus;
  why: string;
  probation: boolean;
}

export interface AgentSummary {
  agent: string;
  n: number;
  passes: number;
  pass_rate: number | null;
  avg_wall_s: number | null;
  avg_step_s: number | null;
  steps: number;
  slow_steps: number;
  slow_step_rate: number | null;
  status: AgentStatus;
  probation: { task_type: string; status: AgentStatus; why: string }[];
}

export interface AgentTable {
  cells: AgentCell[];
  agents: AgentSummary[];
  slow_step_s: number;
  at: number;
}

export interface LiveStep {
  open_step: { tool: string; since: number; seq: number; seconds: number } | null;
  last_activity: number | null;
  idle_s: number | null;
  steps?: number;
  slow_steps?: number;
}
