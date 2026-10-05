-- Branch-serial task job admission schema v4.
-- One task_id is one execution lane. At most one admission may be ADMITTING/ACTIVE.

CREATE TABLE task_job_admissions (
  admission_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  sequence INTEGER NOT NULL CHECK (sequence >= 1),
  operation_id TEXT NOT NULL UNIQUE,
  execution_kind TEXT NOT NULL CHECK (execution_kind IN ('LOCAL','ROUTED')),
  predecessor_job_id TEXT,
  job_id TEXT,
  state TEXT NOT NULL CHECK (state IN ('ADMITTING','ACTIVE','TERMINAL','ABORTED')),
  terminal_state TEXT CHECK (
    terminal_state IS NULL OR terminal_state IN ('SUCCEEDED','FAILED','CANCELLED','LOST')
  ),
  terminal_evidence_json TEXT,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  terminal_at_ms INTEGER,
  UNIQUE(task_id, sequence)
);

CREATE INDEX idx_task_job_admissions_task_sequence
  ON task_job_admissions(task_id,sequence);

CREATE UNIQUE INDEX uq_task_job_admissions_one_active_lane
  ON task_job_admissions(task_id)
  WHERE state IN ('ADMITTING','ACTIVE');
