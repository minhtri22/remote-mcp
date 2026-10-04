# REMOTEMCP_BRANCH_SERIAL_JOB_LIFECYCLE_AND_PREDECESSOR_ADMISSION_IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK

Status: **IMPLEMENTED / QA PENDING**
Date: 2026-10-04
Scope: zero-science implementation and static qualification only. No production deployment.

Bound preregistration:

- `REMOTEMCP_BRANCH_SERIAL_JOB_LIFECYCLE_AND_PREDECESSOR_ADMISSION_PREREGISTRATION`
- preregistration merge: `2558e76df85bb41ee7163a5f860dd79136a7380c`

## Implementation

The implementation introduces schema v4 `task_job_admissions` as an append-only task-lane admission ledger.

The database invariant is:

```text
UNIQUE task_id
WHERE admission.state IN (ADMITTING, ACTIVE)
```

Each admission records a monotonically increasing lane sequence, operation ID, execution kind, predecessor job ID, bound job ID, terminal state and terminal evidence.

The ledger is shared by gateway-local and routed managed execution.

## Local managed path

`TaskJobService.submit()` now:

1. validates the current task lease/worktree/path boundary;
2. treats an already admitted exact operation as replay/resume, not a successor;
3. reconciles the local DurableService before new admission;
4. blocks any authoritative non-terminal predecessor;
5. requires a terminal event/result for the latest predecessor;
6. atomically reserves the task lane;
7. submits the durable job;
8. binds the returned job ID to the admission;
9. releases the lane only after authoritative terminal evidence is observed.

## Routed managed path

`RoutingService.task_job_submit_or_local()` now applies the same lane contract to routed tasks.

Before a new successor admission it refreshes every centrally cached non-terminal predecessor from the bound node using JOB_GET. A row that cannot be resolved on the bound node, including a legacy row with no recoverable node mapping, fails closed with `PREDECESSOR_STATE_UNRESOLVED`.

A terminal routed predecessor must have authoritative JOB_RESULT evidence cached centrally before its admission can become TERMINAL and release the lane.

An exact operation replay uses the existing admission/job instead of treating itself as a predecessor.

## Swarm preservation

No project-wide job mutex is introduced.

Different `task_id` lanes under the same project remain independently admissible and may execute concurrently subject to existing `max_active_tasks`.

Same-lane parallel execution remains forbidden by default.

## Legacy compatibility

Historical job rows are not deleted, rewritten or automatically cancelled.

Admission-time reconciliation can repair a stale routed shadow if the bound node still has authoritative state. Unresolved historical state blocks only that task lane.

`device_status` remains pure read; this implementation does not reintroduce request-time write reconciliation.

## Production boundary

This gate does not:

- deploy schema v4 to the live gateway;
- modify the production SQLite database;
- reconcile or cancel the current live stale rows;
- restart gateway or nodes;
- pair/re-pair devices;
- relaunch scientific jobs.

A separate compatibility/deployment gate is required after static QA PASS.
