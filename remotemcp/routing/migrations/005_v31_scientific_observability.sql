-- V3.1 scientific execution observability and binding lifecycle.

ALTER TABLE routed_jobs ADD COLUMN execution_key TEXT;
ALTER TABLE routed_jobs ADD COLUMN argv_sha256 TEXT;
ALTER TABLE routed_jobs ADD COLUMN cwd TEXT;

CREATE UNIQUE INDEX uq_routed_jobs_execution_key
  ON routed_jobs(execution_key)
  WHERE execution_key IS NOT NULL;

ALTER TABLE project_device_bindings
  ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE'
  CHECK (lifecycle_state IN ('ACTIVE','HISTORICAL'));

ALTER TABLE project_device_bindings
  ADD COLUMN superseded_by_project_id TEXT;

CREATE INDEX idx_project_device_bindings_lifecycle
  ON project_device_bindings(lifecycle_state,device_id);
