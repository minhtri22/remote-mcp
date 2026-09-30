-- V2-A schema v1 PRELOCK ONLY. This file is documentation, not executed by V2-A prelock.
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
PRAGMA synchronous=NORMAL;

CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at_ms INTEGER NOT NULL,
  checksum_sha256 TEXT NOT NULL
);

CREATE TABLE operations (
  operation_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN (
    'RESERVED','EXECUTING','SUCCEEDED',
    'FAILED_RETRYABLE','FAILED_FINAL','IN_DOUBT'
  )),
  principal_key TEXT,
  agent_id TEXT,
  project_id TEXT,
  task_id TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  started_at_ms INTEGER,
  finished_at_ms INTEGER,
  result_json TEXT,
  result_hash TEXT,
  error_code TEXT,
  error_json TEXT
);

CREATE INDEX idx_operations_state ON operations(state);
CREATE INDEX idx_operations_context ON operations(project_id, task_id, agent_id);

CREATE TABLE jobs (
  job_id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL UNIQUE REFERENCES operations(operation_id),
  state TEXT NOT NULL CHECK (state IN (
    'QUEUED','STARTING','RUNNING','CANCELLING',
    'SUCCEEDED','FAILED','CANCELLED','LOST'
  )),
  command_json TEXT NOT NULL,
  cwd_rel TEXT NOT NULL,
  env_profile TEXT NOT NULL,
  launch_nonce TEXT NOT NULL UNIQUE,
  worker_fingerprint_json TEXT,
  payload_fingerprint_json TEXT,
  stdout_path TEXT NOT NULL,
  stderr_path TEXT NOT NULL,
  result_path TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  spawn_started_at_ms INTEGER,
  started_at_ms INTEGER,
  cancel_requested_at_ms INTEGER,
  finished_at_ms INTEGER,
  exit_code INTEGER,
  last_output_at_ms INTEGER,
  last_heartbeat_at_ms INTEGER,
  terminal_event_id INTEGER,
  error_code TEXT,
  error_json TEXT,
  version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0)
);

CREATE INDEX idx_jobs_state ON jobs(state);
CREATE INDEX idx_jobs_operation ON jobs(operation_id);

CREATE TABLE events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT REFERENCES jobs(job_id),
  operation_id TEXT REFERENCES operations(operation_id),
  event_type TEXT NOT NULL,
  terminal INTEGER NOT NULL DEFAULT 0 CHECK (terminal IN (0,1)),
  payload_json TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL
);

CREATE INDEX idx_events_job_event ON events(job_id, event_id);
CREATE INDEX idx_events_operation_event ON events(operation_id, event_id);
CREATE UNIQUE INDEX uq_job_terminal_event
  ON events(job_id)
  WHERE terminal=1 AND job_id IS NOT NULL;

CREATE TABLE event_cursors (
  subscriber_id TEXT NOT NULL,
  job_id TEXT NOT NULL REFERENCES jobs(job_id),
  ack_event_id INTEGER NOT NULL DEFAULT 0 CHECK (ack_event_id >= 0),
  updated_at_ms INTEGER NOT NULL,
  PRIMARY KEY (subscriber_id, job_id)
);
