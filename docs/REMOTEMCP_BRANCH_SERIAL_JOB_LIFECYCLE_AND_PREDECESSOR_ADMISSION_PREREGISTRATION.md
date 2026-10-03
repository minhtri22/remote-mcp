# REMOTEMCP_BRANCH_SERIAL_JOB_LIFECYCLE_AND_PREDECESSOR_ADMISSION_PREREGISTRATION

Status: **FROZEN / PASS**
Date: 2026-10-04
Scope: specification only. No runtime implementation, no production deployment, no job cancellation, and no scientific job relaunch.

Machine-readable source of truth:

- `specs/branch_serial_job_lifecycle_prelock.json`

## Question being locked

For managed RemoteMCP execution, should one project be able to expose multiple concurrent branch/task lanes while each individual lane admits at most one non-terminal durable job at a time, and should every successor job be blocked until the predecessor is authoritatively terminal with durable terminal evidence?

Answer locked by this preregistration: **yes**.

## Confirmed execution abstraction

The serialization unit is **not** the whole project, agent, session, or execution device.

The execution lane is:

`task_id`

For a Git project, that task already maps to an isolated task branch/worktree:

```text
Project P
├── Task/Branch A / Worktree A
│   └── Job A1 -> TERMINAL -> Job A2 -> TERMINAL -> Job A3 ...
├── Task/Branch B / Worktree B
│   └── Job B1 -> TERMINAL -> Job B2 -> ...
└── Task/Branch C / Worktree C
    └── Job C1 -> TERMINAL -> Job C2 -> ...
```

For a NON_GIT project there is no Git branch/worktree, but `task_id` remains the execution lane and receives the same serial-job admission rule.

Different task lanes may run concurrently, subject to the project's existing `max_active_tasks` capacity.

This preserves swarm use:

```text
one project
  -> N task lanes
     -> N lease owners/agents may work concurrently
        -> each lane is internally serial
```

No project-wide single-job lock is introduced.

## Lease ownership

A branch/task lane is owned by the **current task lease**, not permanently by an agent identity.

Exactly one session owns the active task lease at a time.

If a lease expires and the task becomes RECOVERABLE, a later claimant inherits the same task lane, branch/worktree, job history and predecessor barrier. Takeover does not reset the lane or create permission to overlap a still-running predecessor.

A launched durable job remains independent of agent heartbeat exactly as frozen in V2-B.

## Observed pre-registration evidence

Read-only/control-plane inspection before this preregistration confirmed that the current architecture already matches the intended project-to-task/worktree abstraction, but does **not** enforce serial job admission inside a task lane.

Observed examples included:

- a Git project configured for more than one active task, proving project-level multi-lane concurrency is already part of the model;
- a routed task with many terminal jobs plus a centrally cached non-terminal row created before later jobs that had already reached terminal states;
- another task lane with the same pattern;
- one stale central QUEUED row with a valid node job mapping that refreshed authoritatively to SUCCEEDED, reducing the device's cached active-routed-job count by one;
- a separate QUEUED row with no node job ID that could not be resolved by direct JOB_GET.

Therefore:

1. `task_jobs` history can contain stale non-terminal shadow state;
2. the current submit path can admit later jobs without first proving all prior same-lane work terminal;
3. `active_routed_jobs` can be inflated by stale routed-job state;
4. the problem is lifecycle/admission accounting, not evidence that all cached "active" rows correspond to live processes.

No job was cancelled, deleted, restarted or relaunched during this pre-registration inspection.

## Terminal contract

Terminal job states are exactly:

- `SUCCEEDED`
- `FAILED`
- `CANCELLED`
- `LOST`

Non-terminal states include:

- `QUEUED`
- `STARTING`
- `RUNNING`
- `CANCELLING`

A predecessor does **not** need to succeed before a successor may run. A failed/cancelled/lost predecessor can be followed by a diagnostic, recovery or successor job.

However, the predecessor must satisfy both:

```text
terminal state
AND
authoritative terminal evidence
```

A cached terminal label without authoritative durable evidence is not sufficient.

Terminal authority remains the durable runtime/reconciler:

- natural process completion -> SUCCEEDED / FAILED;
- ownership-verified cancellation -> CANCELLED;
- durable reconciliation with unrecoverable process loss -> LOST.

There will be **no agent-facing manual `end_job()`** that lets an agent declare its own job complete.

## Terminal evidence

For routed jobs, terminal evidence must come from the bound node's authoritative durable state, normally via JOB_RESULT or equivalent durable terminal reconciliation, and be cached centrally.

For gateway-local managed jobs, terminal evidence comes from the gateway-local durable terminal record/event.

For LOST, the reconciliation reason is part of terminal evidence.

The successor barrier is evidence-based rather than dependent on whether the previous chat/agent remembered to poll the job.

## Successor admission protocol

For a new `task_job_submit`, the prospective implementation is frozen to the following semantics:

```text
request
  -> validate current task lease
  -> exact operation_id already exists?
       YES -> resume/replay the same operation
              (not a new successor; must not self-block)
       NO
  -> resolve task_id lane
  -> reconcile cached non-terminal predecessor candidates
  -> predecessor unresolved/non-terminal?
       YES -> BLOCK
       NO
  -> atomically reserve one lane admission
  -> dispatch local/routed durable job
  -> bind job identity to admission
  -> keep lane closed to successors
       until authoritative terminal evidence is persisted
```

An operation replay is checked before new-successor admission semantics so that a lost response cannot cause the original submit to be blocked by its own already-created job.

## Reconciliation before admission

The barrier must not trust stale central cache blindly.

For a routed predecessor with a known `node_job_id` and ONLINE bound device:

```text
central non-terminal cache
  -> JOB_GET / durable node state
  -> refresh central shadow
  -> if terminal, obtain/cache terminal evidence
```

For a central routed row with no `node_job_id`, implementation must reconcile the originating routed command/operation first.

It is forbidden to manufacture LOST merely because the node-job mapping is absent.

If the predecessor cannot be resolved because the device is offline, command history is ambiguous, or evidence is otherwise unavailable:

`PREDECESSOR_STATE_UNRESOLVED`

The lane fails closed. No replacement/successor job starts.

For gateway-local managed jobs, the same rule applies using the gateway-local DurableService as authority.

## Atomic race rule

Two genuinely new submissions to the same `task_id` must never both pass the predecessor check.

Implementation therefore requires a single control-plane admission authority capable of atomically establishing:

```text
at most one ADMITTING/ACTIVE job admission per task lane
```

The implementation may use a dedicated lane/admission ledger, but the exact schema is deferred to the implementation static-preflight gate.

Required behavior is already frozen:

- same operation ID -> idempotent replay/resume;
- two different new operation IDs racing on one lane -> at most one admits;
- crash after admission reservation -> reconcile that same reservation/operation;
- crash ambiguity must not be repaired by launching another successor.

Frozen error classes:

- `PREDECESSOR_JOB_NOT_TERMINAL`
- `PREDECESSOR_STATE_UNRESOLVED`
- `TASK_JOB_ADMISSION_CONFLICT`
- `TASK_JOB_LANE_STATE_MISMATCH`

## Existing local and routed paths

This gate applies to **all managed task job submission**, not only execution-device routing.

Current local path:

`TaskJobService.submit()`

Current routed path:

`RoutingService.task_job_submit_or_local()`

Both currently validate the task lease and then can reach durable submission without a same-task predecessor barrier.

The successor implementation must not leave one path serial and the other path parallel.

## Legacy rollout rule

Existing historical records are evidence and must not be deleted or rewritten simply to make the new invariant pass.

Before enforcement reaches production, a bounded compatibility audit/reconciliation must classify pre-existing non-terminal rows.

Rules:

- refresh resolvable stale rows from authoritative durable state;
- preserve all terminal history;
- do not bulk-cancel legacy rows;
- do not delete job records;
- do not rewrite historical terminal outcomes;
- unresolved legacy non-terminal state blocks **new jobs only in that same task lane**;
- other task lanes in the project remain unaffected.

This makes rollout fail-closed without turning one bad historical lane into a project-wide outage.

## active_routed_jobs semantics

The earlier SQLite read-path fix remains binding: `device_status` must remain a pure read path.

Therefore this gate explicitly forbids request-time reconciliation writes from `device_status`.

After implementation, `active_routed_jobs` should represent reconciled non-terminal routed admissions/state, not indefinitely stale historical shadows. Reconciliation belongs in admission/recovery/background machinery, not in the status read itself.

The node heartbeat's `active_node_jobs` may be used as diagnostic discrepancy evidence, but it is not authoritative enough to terminalize central job history by itself.

## Swarm compatibility

This gate intentionally preserves swarm execution.

Parallelism unit:

`task lane`

Serialization unit:

`task lane`

A swarm may create/claim multiple task lanes under one project and run those lanes concurrently, subject to existing project capacity.

This gate does **not** implement:

- parent/child swarm task groups;
- fan-out orchestration;
- join barriers;
- same-lane parallel jobs.

A future swarm join gate may depend on terminal outputs from multiple lanes. It does not require weakening same-lane serialization.

If same-branch parallel execution is ever required, it must be opened prospectively as a separate explicit concurrency protocol rather than becoming an implicit bypass of this barrier.

## Public API compatibility

The existing `task_job_submit` public signature remains the target.

No `end_job` public API is introduced.

Optional read-only observability fields may later expose:

- lane sequence;
- predecessor job ID;
- admission state.

Those fields do not alter terminal authority.

## Explicitly forbidden in this preregistration

- runtime implementation;
- schema migration;
- production deployment;
- cancelling or deleting current jobs;
- manually marking a job terminal;
- restarting an execution node;
- pairing/re-pairing;
- migrating projects/tasks between devices;
- using a research task as infrastructure transport;
- relaunching any scientific job;
- adding same-lane parallel mode;
- implementing swarm join/orchestration.

## Qualification requirements for the successor gate

The implementation static-preflight must include zero-science fixtures proving at least:

1. one task: J1 active -> J2 rejected;
2. J1 terminal + evidence -> J2 admitted;
3. J1 FAILED/CANCELLED/LOST + evidence -> successor admitted;
4. cached RUNNING but authoritative SUCCEEDED -> reconcile then admit;
5. routed row with missing node_job_id and unresolved command -> fail closed;
6. device offline with unresolved predecessor -> fail closed;
7. same operation replay -> returns/resumes same job and does not self-block;
8. two concurrent distinct submits to same lane -> exactly one admission;
9. crash/recovery around admission reservation -> no duplicate launch;
10. two different task lanes in one project -> concurrent jobs allowed;
11. lease takeover -> predecessor barrier preserved;
12. local managed path and routed path enforce equivalent semantics;
13. `device_status` remains a pure-read path;
14. legacy reconciliation never deletes/cancels historical records;
15. active-routed count drops when stale terminal shadows are reconciled;
16. public repo/privacy hygiene remains PASS.

No research repository or scientific workload may be used for those qualification fixtures.

## Preregistered successor

Only after this preregistration is frozen may implementation begin:

`REMOTEMCP_BRANCH_SERIAL_JOB_LIFECYCLE_AND_PREDECESSOR_ADMISSION_IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK`


## Executable preregistration QA lock

GitHub Actions run:

`37162418075`

Cross-platform result:

- Windows: `15 passed`
- Ubuntu: `15 passed`

Frozen source identities at the first complete prelock QA:

- `specs/branch_serial_job_lifecycle_prelock.json`: `75e04293f08145e399142aa40813671d13a3ae1b`
- `tests/test_branch_serial_job_lifecycle_prelock.py`: `7c8f530eb5f5666b2e65317ac1f4f2a3b3d7c2e6`
- `.github/workflows/branch-serial-job-lifecycle-prelock-qa.yml`: `72d2d977d5c170a6d213a73781e85044f770ff56`

The preregistration QA verifies the machine-readable invariants for per-task serialization, terminal evidence, idempotent operation replay, fail-closed unresolved predecessors, legacy-history preservation, pure-read device status, and swarm-compatible multi-lane execution.

No production runtime, execution node, project, task, durable job, routed job, or scientific workload was mutated by the preregistration implementation itself.

## Gate adjudication

`REMOTEMCP_BRANCH_SERIAL_JOB_LIFECYCLE_AND_PREDECESSOR_ADMISSION_PREREGISTRATION = PASS`

The next valid gate is:

`REMOTEMCP_BRANCH_SERIAL_JOB_LIFECYCLE_AND_PREDECESSOR_ADMISSION_IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK`
