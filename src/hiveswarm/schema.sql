PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS tasks (
  id            TEXT PRIMARY KEY,
  project       TEXT NOT NULL,
  spec          TEXT NOT NULL,
  acceptance    TEXT,
  repo_path     TEXT NOT NULL,
  base_ref      TEXT NOT NULL,
  worktree      TEXT,
  state         TEXT NOT NULL DEFAULT 'pending',
  claimed_by    TEXT,
  lease_expires INTEGER,
  attempts      INTEGER NOT NULL DEFAULT 0,
  max_attempts  INTEGER NOT NULL DEFAULT 3,
  vault_refs    TEXT,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project);

CREATE TABLE IF NOT EXISTS classifications (
  task_id         TEXT PRIMARY KEY REFERENCES tasks(id),
  task_type       TEXT NOT NULL,
  type_conf       REAL NOT NULL,
  difficulty      REAL NOT NULL,
  difficulty_conf REAL NOT NULL,
  reasoning       REAL,
  needs_tools     REAL,
  needs_vision    REAL,
  is_multistep    REAL,
  source          TEXT NOT NULL,
  raw             TEXT,
  created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
  id            TEXT PRIMARY KEY,
  task_id       TEXT NOT NULL REFERENCES tasks(id),
  agent         TEXT NOT NULL,
  model         TEXT,
  started_at    INTEGER NOT NULL,
  ended_at      INTEGER,
  outcome       TEXT,
  tokens_in     INTEGER,
  tokens_out    INTEGER,
  usd_cost      REAL,
  quota_units   REAL,
  wall_seconds  REAL,
  verifier_log  TEXT,
  diff_stat     TEXT,
  branch        TEXT
);

CREATE INDEX IF NOT EXISTS idx_attempts_task ON attempts(task_id);

CREATE TABLE IF NOT EXISTS agent_stats (
  agent         TEXT NOT NULL,
  task_type     TEXT NOT NULL,
  diff_band     INTEGER NOT NULL,
  alpha         REAL NOT NULL DEFAULT 1.0,
  beta          REAL NOT NULL DEFAULT 1.0,
  n             INTEGER NOT NULL DEFAULT 0,
  mean_usd      REAL,
  mean_quota    REAL,
  mean_seconds  REAL,
  updated_at    INTEGER,
  PRIMARY KEY (agent, task_type, diff_band)
);

CREATE TABLE IF NOT EXISTS quota_windows (
  agent           TEXT PRIMARY KEY,
  window_kind     TEXT NOT NULL,
  window_start    INTEGER NOT NULL,
  window_end      INTEGER NOT NULL,
  limit_units     REAL,
  used_units      REAL NOT NULL DEFAULT 0,
  reserve_frac    REAL NOT NULL DEFAULT 0.15,
  source          TEXT NOT NULL,
  updated_at      INTEGER
);

CREATE TABLE IF NOT EXISTS task_logs (
  task_id     TEXT NOT NULL REFERENCES tasks(id),
  seq         INTEGER NOT NULL,
  ts          INTEGER NOT NULL,
  source      TEXT NOT NULL,
  chunk       TEXT NOT NULL,
  PRIMARY KEY (task_id, seq)
);

CREATE TABLE IF NOT EXISTS agents (
  agent_id      TEXT PRIMARY KEY,
  host          TEXT,
  capabilities  TEXT NOT NULL,
  last_seen     INTEGER NOT NULL,
  registered_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_cache (
  key         TEXT PRIMARY KEY,
  model       TEXT NOT NULL,
  answers     TEXT NOT NULL,
  source      TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  hits        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS session_commands (
  id          TEXT PRIMARY KEY,
  task_id     TEXT NOT NULL REFERENCES tasks(id),
  host        TEXT,
  kind        TEXT NOT NULL,
  payload     TEXT,
  created_at  INTEGER NOT NULL,
  taken_at    INTEGER,
  done_at     INTEGER,
  result      TEXT
);

CREATE INDEX IF NOT EXISTS idx_session_commands_pending ON session_commands(host, taken_at);

CREATE TABLE IF NOT EXISTS directives (
  id            TEXT PRIMARY KEY,
  task_id       TEXT REFERENCES tasks(id),
  project       TEXT,
  text          TEXT NOT NULL,
  every_tools   INTEGER NOT NULL DEFAULT 8,
  every_minutes INTEGER NOT NULL DEFAULT 10,
  check_jev     INTEGER NOT NULL DEFAULT 1,
  created_by    TEXT,
  created_at    INTEGER NOT NULL,
  active        INTEGER NOT NULL DEFAULT 1,
  fires         INTEGER NOT NULL DEFAULT 0,
  violations    INTEGER NOT NULL DEFAULT 0,
  last_fired_at INTEGER,
  last_result   TEXT
);

CREATE INDEX IF NOT EXISTS idx_directives_task ON directives(task_id, active);
CREATE INDEX IF NOT EXISTS idx_directives_project ON directives(project, active);

CREATE TABLE IF NOT EXISTS project_local (
  project     TEXT PRIMARY KEY,
  host        TEXT,
  path        TEXT,
  win_path    TEXT,
  head        TEXT,
  synced_at   INTEGER,
  dirty       INTEGER NOT NULL DEFAULT 0,
  ahead       INTEGER NOT NULL DEFAULT 0,
  behind      INTEGER NOT NULL DEFAULT 0,
  note        TEXT
);

CREATE TABLE IF NOT EXISTS project_tombstones (
  name        TEXT PRIMARY KEY,
  purge       INTEGER NOT NULL DEFAULT 0,
  repo_path   TEXT,
  at          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS deferred (
  id          TEXT PRIMARY KEY,
  project     TEXT NOT NULL,
  task_id     TEXT,
  attempt_id  TEXT,
  agent       TEXT,
  text        TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  resolved_at INTEGER,
  resolved_by TEXT,
  note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_deferred_project ON deferred(project, resolved_at);

CREATE TABLE IF NOT EXISTS plans (
  project     TEXT PRIMARY KEY,
  text        TEXT NOT NULL,
  updated_at  INTEGER NOT NULL,
  updated_by  TEXT
);

CREATE TABLE IF NOT EXISTS pauses (
  project     TEXT PRIMARY KEY,
  paused_at   INTEGER NOT NULL,
  paused_by   TEXT,
  reason      TEXT
);

CREATE TABLE IF NOT EXISTS briefs (
  id            TEXT PRIMARY KEY,
  project       TEXT NOT NULL,
  text          TEXT NOT NULL,
  created_by    TEXT,
  created_at    INTEGER NOT NULL,
  delivered_at  INTEGER,
  delivered_via TEXT
);

CREATE INDEX IF NOT EXISTS idx_briefs_project ON briefs(project, delivered_at);

CREATE TABLE IF NOT EXISTS advisor_messages (
  id          TEXT PRIMARY KEY,
  project     TEXT NOT NULL,
  turn_id     TEXT,
  role        TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'text',
  text        TEXT NOT NULL,
  ts          REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_advisor_messages_project ON advisor_messages(project, ts);

CREATE TABLE IF NOT EXISTS advisor_turns (
  id                TEXT PRIMARY KEY,
  project           TEXT NOT NULL,
  text              TEXT NOT NULL,
  created_at        INTEGER NOT NULL,
  taken_at          INTEGER,
  host              TEXT,
  done_at           INTEGER,
  error             TEXT
);

CREATE TABLE IF NOT EXISTS advisors (
  project            TEXT PRIMARY KEY,
  claude_session_id  TEXT,
  host               TEXT,
  updated_at         INTEGER NOT NULL
);
