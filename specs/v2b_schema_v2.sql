-- V2-B schema extension PRELOCK ONLY. Not executed by this gate.
CREATE TABLE agents (
  agent_id TEXT PRIMARY KEY,
  principal_key TEXT NOT NULL,
  client_instance_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  UNIQUE(principal_key, client_instance_id)
);

CREATE TABLE agent_sessions (
  session_id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES agents(agent_id),
  state TEXT NOT NULL CHECK (state IN ('ACTIVE','STALE','CLOSED')),
  heartbeat_seq INTEGER NOT NULL DEFAULT 0 CHECK (heartbeat_seq >= 0),
  created_at_ms INTEGER NOT NULL,
  last_heartbeat_at_ms INTEGER NOT NULL,
  closed_at_ms INTEGER
);
CREATE INDEX idx_agent_sessions_agent_state ON agent_sessions(agent_id,state);

CREATE TABLE projects (
  project_id TEXT PRIMARY KEY,
  root_rel TEXT NOT NULL UNIQUE,
  project_kind TEXT NOT NULL CHECK (project_kind IN ('GIT','NON_GIT')),
  max_active_tasks INTEGER NOT NULL DEFAULT 4 CHECK (max_active_tasks BETWEEN 1 AND 32),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL
);

CREATE TABLE tasks (
  task_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  title TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN (
    'CREATED','READY','CLAIMED','RUNNING','BLOCKED','RECOVERABLE',
    'COMPLETED','FAILED','CANCELLED'
  )),
  base_ref TEXT NOT NULL,
  base_commit TEXT,
  branch_name TEXT,
  worktree_rel TEXT,
  owner_agent_id TEXT REFERENCES agents(agent_id),
  owner_session_id TEXT REFERENCES agent_sessions(session_id),
  lease_epoch INTEGER NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
  cleanup_pending INTEGER NOT NULL DEFAULT 0 CHECK (cleanup_pending IN (0,1)),
  checkpoint_json TEXT,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  claimed_at_ms INTEGER,
  finished_at_ms INTEGER
);
CREATE INDEX idx_tasks_project_state ON tasks(project_id,state);
CREATE INDEX idx_tasks_owner ON tasks(owner_agent_id,owner_session_id);

CREATE TABLE task_leases (
  task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
  agent_id TEXT NOT NULL REFERENCES agents(agent_id),
  session_id TEXT NOT NULL REFERENCES agent_sessions(session_id),
  lease_epoch INTEGER NOT NULL CHECK (lease_epoch >= 1),
  lease_token_hash TEXT NOT NULL,
  acquired_at_ms INTEGER NOT NULL,
  renewed_at_ms INTEGER NOT NULL,
  expires_at_ms INTEGER NOT NULL
);
CREATE INDEX idx_task_leases_session ON task_leases(session_id,expires_at_ms);

CREATE TABLE path_leases (
  path_lease_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  agent_id TEXT NOT NULL REFERENCES agents(agent_id),
  session_id TEXT NOT NULL REFERENCES agent_sessions(session_id),
  lease_epoch INTEGER NOT NULL CHECK (lease_epoch >= 1),
  path_rel TEXT NOT NULL,
  scope TEXT NOT NULL CHECK (scope IN ('FILE','TREE')),
  mode TEXT NOT NULL CHECK (mode='WRITE_EXCLUSIVE'),
  acquired_at_ms INTEGER NOT NULL,
  renewed_at_ms INTEGER NOT NULL,
  expires_at_ms INTEGER NOT NULL
);
CREATE INDEX idx_path_leases_project_path ON path_leases(project_id,path_rel);
CREATE INDEX idx_path_leases_task ON path_leases(task_id);

CREATE TABLE task_events (
  task_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  operation_id TEXT,
  agent_id TEXT REFERENCES agents(agent_id),
  session_id TEXT REFERENCES agent_sessions(session_id),
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL
);
CREATE INDEX idx_task_events_task_event ON task_events(task_id,task_event_id);

CREATE TABLE task_checkpoints (
  checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  operation_id TEXT NOT NULL UNIQUE,
  agent_id TEXT NOT NULL REFERENCES agents(agent_id),
  session_id TEXT NOT NULL REFERENCES agent_sessions(session_id),
  lease_epoch INTEGER NOT NULL,
  summary TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL
);
CREATE INDEX idx_task_checkpoints_task ON task_checkpoints(task_id,checkpoint_id);
