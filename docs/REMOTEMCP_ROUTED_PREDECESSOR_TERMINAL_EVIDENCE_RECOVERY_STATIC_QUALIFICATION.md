# REMOTEMCP_ROUTED_PREDECESSOR_TERMINAL_EVIDENCE_RECOVERY_STATIC_QUALIFICATION

Status: **SOURCE PATCH / NOT DEPLOYED**

## Problem

A routed task lane may remain blocked after its predecessor has already reached a
terminal durable state if the gateway did not cache `JOB_RESULT` or the node-local
`proxy_job_id -> node_job_id` mapping was lost/stale during a gateway or transport
outage.

The previous fail-closed behavior was scientifically safe but could make a task lane
permanently unusable even though the exact durable predecessor and provenance evidence
still existed on the execution node.

## Recovery contract

The recovery path never launches or resubmits work.

The gateway may request `JOB_RECOVER_ROUTED_JOB` only when it already has the exact
central predecessor identity, including the exact `node_job_id`. The node then:

1. reads the existing durable job by exact `node_job_id`;
2. verifies durable `task_id` and `project_id`;
3. independently proves the requested proxy mapping from preserved job provenance or
   exactly-once science-execution metadata;
4. verifies available submitted argv hash, cwd, and execution key constraints;
5. reconstructs the node-local routed mapping only after those checks pass;
6. returns current durable state without starting a process;
7. if terminal, returns the existing durable terminal result so the gateway can cache
   terminal evidence and release the task lane.

If identity proof is missing or mismatched, recovery returns
`PREDECESSOR_STATE_UNRESOLVED` and the lane remains blocked.

If the exact recovered job is still non-terminal, the normal
`PREDECESSOR_JOB_NOT_TERMINAL` admission rule remains in force.

## Scientific safety

This repair does not:

- rerun or relaunch the predecessor;
- manufacture a terminal state;
- infer success from process absence;
- release a lane from central registry state alone;
- cancel any active job;
- modify scientific evidence or `lineage.md`.

## Qualification cases

Required on Windows and Ubuntu:

1. normal terminal readback remains valid;
2. terminal durable predecessor + missing node proxy mapping + preserved provenance
   reconstructs the mapping, caches authoritative terminal evidence, and admits the
   successor;
3. provenance/task/project/hash mismatch remains fail-closed and does not reconstruct
   the mapping;
4. unresolved legacy predecessor without an exact `node_job_id` remains blocked;
5. different task lanes remain concurrently admissible;
6. full V2-B and V2-BD regressions pass.

This is source-only qualification. It does not authorize a live machine-1 restart,
Zero-C migration, long-run cancellation, re-pair, or device identity change.
