-- V2-BD central routing schema v3 PRELOCK ONLY. Not executed by this gate.

CREATE TABLE devices (
  device_id TEXT PRIMARY KEY,
  owner_account_id TEXT NOT NULL REFERENCES owner_accounts(owner_account_id),
  device_name TEXT NOT NULL,
  public_key_b64 TEXT NOT NULL,
  key_fingerprint_sha256 TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL CHECK (state IN ('ONLINE','OFFLINE','REVOKED')),
  route_generation INTEGER NOT NULL DEFAULT 1 CHECK (route_generation >= 1),
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  platform_json TEXT NOT NULL DEFAULT '{}',
  paired_at_ms INTEGER NOT NULL,
  last_seen_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER
);
CREATE INDEX idx_devices_owner_state ON devices(owner_account_id,state);

CREATE TABLE device_pairings (
  pairing_id TEXT PRIMARY KEY,
  owner_account_id TEXT NOT NULL REFERENCES owner_accounts(owner_account_id),
  requested_name TEXT NOT NULL,
  code_hash_sha256 TEXT NOT NULL UNIQUE,
  created_at_ms INTEGER NOT NULL,
  expires_at_ms INTEGER NOT NULL,
  used_at_ms INTEGER,
  paired_device_id TEXT REFERENCES devices(device_id)
);
CREATE INDEX idx_device_pairings_expiry ON device_pairings(expires_at_ms);

CREATE TABLE device_request_nonces (
  device_id TEXT NOT NULL REFERENCES devices(device_id),
  nonce TEXT NOT NULL,
  seen_at_ms INTEGER NOT NULL,
  expires_at_ms INTEGER NOT NULL,
  PRIMARY KEY(device_id,nonce)
);
CREATE INDEX idx_device_nonces_expiry ON device_request_nonces(expires_at_ms);

CREATE TABLE project_device_bindings (
  project_id TEXT PRIMARY KEY REFERENCES projects(project_id),
  device_id TEXT NOT NULL REFERENCES devices(device_id),
  binding_generation INTEGER NOT NULL DEFAULT 1 CHECK (binding_generation >= 1),
  bound_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL
);
CREATE INDEX idx_project_device_bindings_device ON project_device_bindings(device_id);

CREATE TABLE task_device_bindings (
  task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  device_id TEXT NOT NULL REFERENCES devices(device_id),
  binding_generation INTEGER NOT NULL CHECK (binding_generation >= 1),
  inherited_at_ms INTEGER NOT NULL
);
CREATE INDEX idx_task_device_bindings_device ON task_device_bindings(device_id);
CREATE INDEX idx_task_device_bindings_project ON task_device_bindings(project_id);

CREATE TABLE device_commands (
  command_id TEXT PRIMARY KEY,
  device_id TEXT NOT NULL REFERENCES devices(device_id),
  route_generation INTEGER NOT NULL CHECK (route_generation >= 1),
  operation_id TEXT REFERENCES operations(operation_id),
  operation_step INTEGER NOT NULL DEFAULT 0 CHECK (operation_step >= 0),
  project_id TEXT REFERENCES projects(project_id),
  task_id TEXT REFERENCES tasks(task_id),
  command_type TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN (
    'QUEUED','LEASED','SUCCEEDED','FAILED','CANCELLED','IN_DOUBT'
  )),
  delivery_attempt INTEGER NOT NULL DEFAULT 0 CHECK (delivery_attempt >= 0),
  lease_expires_at_ms INTEGER,
  command_expires_at_ms INTEGER NOT NULL,
  result_json TEXT,
  error_code TEXT,
  error_json TEXT,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  finished_at_ms INTEGER
);
CREATE INDEX idx_device_commands_dispatch ON device_commands(device_id,state,created_at_ms);
CREATE INDEX idx_device_commands_task ON device_commands(task_id,created_at_ms);
CREATE UNIQUE INDEX uq_device_commands_operation_step
  ON device_commands(operation_id,operation_step)
  WHERE operation_id IS NOT NULL;

CREATE TABLE routed_jobs (
  proxy_job_id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL UNIQUE REFERENCES operations(operation_id),
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  device_id TEXT NOT NULL REFERENCES devices(device_id),
  node_job_id TEXT,
  last_known_state TEXT NOT NULL,
  last_seen_at_ms INTEGER NOT NULL,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  terminal_at_ms INTEGER
);
CREATE INDEX idx_routed_jobs_task ON routed_jobs(task_id,created_at_ms);
CREATE INDEX idx_routed_jobs_device_state ON routed_jobs(device_id,last_known_state);

CREATE TABLE device_events (
  device_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL REFERENCES devices(device_id),
  event_type TEXT NOT NULL,
  command_id TEXT REFERENCES device_commands(command_id),
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at_ms INTEGER NOT NULL
);
CREATE INDEX idx_device_events_device_event ON device_events(device_id,device_event_id);
