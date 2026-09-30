-- RemoteMCP V2-BD execution-node local schema v1 PRELOCK ONLY.

CREATE TABLE node_meta (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL
);

CREATE TABLE node_projects (
  project_id TEXT PRIMARY KEY,
  binding_generation INTEGER NOT NULL CHECK (binding_generation >= 1),
  root_rel TEXT NOT NULL,
  project_kind TEXT NOT NULL CHECK (project_kind IN ('GIT','NON_GIT')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL
);

CREATE TABLE node_tasks (
  task_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES node_projects(project_id),
  binding_generation INTEGER NOT NULL CHECK (binding_generation >= 1),
  worktree_rel TEXT,
  branch_name TEXT,
  state TEXT NOT NULL CHECK (state IN ('ACTIVE','TERMINAL')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL
);

CREATE TABLE node_commands (
  command_id TEXT PRIMARY KEY,
  route_generation INTEGER NOT NULL CHECK (route_generation >= 1),
  operation_id TEXT,
  request_hash TEXT NOT NULL,
  command_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN (
    'RECEIVED','EXECUTING','SUCCEEDED','FAILED','IN_DOUBT'
  )),
  result_json TEXT,
  error_code TEXT,
  error_json TEXT,
  received_at_ms INTEGER NOT NULL,
  started_at_ms INTEGER,
  finished_at_ms INTEGER
);

CREATE TABLE node_cas_mutations (
  command_id TEXT PRIMARY KEY REFERENCES node_commands(command_id),
  task_id TEXT NOT NULL,
  path_rel TEXT NOT NULL,
  expected_before_hash TEXT NOT NULL,
  intended_after_hash TEXT NOT NULL,
  temp_rel TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('PREPARED','REPLACED','COMMITTED','ABORTED')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL
);
CREATE INDEX idx_node_cas_task_state ON node_cas_mutations(task_id,state);

CREATE TABLE node_routed_jobs (
  proxy_job_id TEXT PRIMARY KEY,
  node_job_id TEXT NOT NULL UNIQUE,
  task_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  state TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  terminal_at_ms INTEGER
);
CREATE INDEX idx_node_routed_jobs_task ON node_routed_jobs(task_id,created_at_ms);
