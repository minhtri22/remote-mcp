# REMOTEMCP_BRANCH_SERIAL_JOB_LIFECYCLE_AND_PREDECESSOR_ADMISSION_IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK

Status: **PASS / EXECUTION LOCKED — NOT DEPLOYED**
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


## Executable QA lock

Primary implementation QA run:

`37165068554`

Ubuntu:

- targeted local admission suite: `7 passed`
- targeted routed/migration suite: `6 passed`
- full V2-B regression: `30 passed`
- full V2-BD regression: `43 passed, 2 skipped`
- public hygiene: `6 passed`

Windows:

- targeted local admission suite: `7 passed`
- targeted routed/migration suite: `6 passed`
- full V2-B regression: `30 passed`
- full V2-BD regression: `45 passed`
- public hygiene: `6 passed`

Migration checksum/newline compatibility run:

`37165068551`

- Ubuntu: PASS
- Windows: PASS
- existing device read-path regression: PASS on both platforms

The first implementation QA attempt failed only because one pytest process collected V2-B and V2-BD directories together and imported the wrong directory-local `conftest.py`. The harness was split into isolated V2-B and V2-BD invocations; no runtime logic was changed to rescue that collection failure.

## Frozen implementation identities

- schema v4 spec: `e2e9a35a18558bcea6e6369446a8a612a300da7c`
- schema v4 runtime migration: `e2e9a35a18558bcea6e6369446a8a612a300da7c`
- admission ledger: `378a7ab7cffbc17176adeb5b2f035e372ae91131`
- central DB bootstrap: `e8f2b62e4e1269ee08b67faf5afb80aaa681c25c`
- multi-agent service wiring: `72a9975ba6e0a10ab9673a51a69c336a4c1652fc`
- local task-job service: `f89b3d94f4c5efc8395b4d15abf0cfcc02342c26`
- routed-job repository: `8731a17acca8c1390d4bbaa5e27c2e6b3b529b81`
- routing service: `0c25abacc42338cc146ad448632c10c77b08ca44`
- migration qualification: `1cf281536d2e04ae34a9dea664e18d8404f3a972`
- local admission qualification: `55834aad65b2c334025ef37ae301fd992a398f80`
- routed admission qualification: `d23113a50bfcf03ae533856c69efa633052b2969`
- QA workflow: `1ad859f38a734bec646cf9f0798a363d5fe78375`

The schema spec and executable migration are byte-identical.

## Gate adjudication

`REMOTEMCP_BRANCH_SERIAL_JOB_LIFECYCLE_AND_PREDECESSOR_ADMISSION_IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK = PASS`

The implementation is locked for compatibility audit. It is **not authorized for production deployment yet** because the live control plane contains pre-v4 routed-job history, including stale/unresolved non-terminal shadows observed before this gate.

The next valid gate is:

`REMOTEMCP_BRANCH_SERIAL_JOB_LIFECYCLE_LEGACY_NONTERMINAL_COMPATIBILITY_AUDIT_AND_PRODUCTION_DEPLOYMENT_PREFLIGHT`

That successor must be read-only against live state first, classify every legacy non-terminal routed row that can affect admission, prove migration v4 on a production-state backup, and define the safe rollout boundary before any gateway cutover.
