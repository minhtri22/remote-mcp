# RemoteMCP V3.1 technical architecture and rolling-upgrade guide

Date: 2026-10-08

Status: **qualified-source documentation / live deployment must be verified independently**

This document describes the V3.1 production-recovery architecture and the safe upgrade boundary for Windows execution nodes that may still host long-running scientific processes.

## 1. Architectural layers

RemoteMCP separates control-plane state from execution processes:

```text
Authorized client
      |
      v
RemoteMCP gateway
  OAuth / registry / routing / task admission
      |
      v
outbound execution node
  project binding / worktree / routed jobs / process attestation
      |
      v
durable worker -> scientific/model payload
```

A routed task is bound to one project and one execution device. Placement is inherited; it is not selected independently by each job.

## 2. Branch-serial admission

Branch-serial admission is scoped to one `task_id`.

```text
task A: job A1 ------------------> terminal
                               \--> job A2 admitted

task B: job B1 ------------------------------>
task C: job C1 ---------------------->
```

Jobs from different task lanes may run concurrently on the same execution device subject to normal capacity limits. There is no project-wide or machine-wide mutex introduced by branch-serial admission.

The admission gate is fail-closed when the predecessor state is not authoritative.

## 3. Routed predecessor terminal-evidence recovery

A gateway outage may leave central state without a cached terminal result even when the exact durable job has already completed on the node.

V3.1 adds an internal recovery command:

```text
central predecessor row
  proxy_job_id
  exact node_job_id
  task_id
  project_id
  argv/cwd/execution-key constraints
        |
        v
JOB_RECOVER_ROUTED_JOB
        |
        +-- durable operation task/project identity
        +-- preserved job provenance or exactly-once science metadata
        +-- exact node_job_id
        |
        v
existing durable state/result only
```

Recovery does not launch a new payload. If identity proof is incomplete or mismatched, the task lane remains blocked with `PREDECESSOR_STATE_UNRESOLVED`.

## 4. Managed environment propagation

Durable workers intentionally use a reduced child environment. V3.1 keeps that fail-closed boundary while allowing explicit managed credentials:

- `NVIDIA_API_KEY` is allowlisted by default;
- extra names may be declared with `REMOTEMCP_MANAGED_ENV_ALLOWLIST`;
- arbitrary parent environment variables remain excluded;
- on Windows, an allowlisted variable missing from the already-running node process may be read from the persistent User or Machine environment;
- the propagation mechanism does not write secret values into command JSON, registry rows, provenance, or scientific evidence.

## 5. Physical-process safety

Durable state is not equivalent to the Windows process table.

A process can remain alive after the control-plane job row becomes terminal, or it can be an intentionally long-lived model/scientific process that was not launched through the current routed durable path.

The maintenance gate therefore distinguishes:

```text
durable capacity truth
        +
physical process snapshot
        =
maintenance readiness
```

The node samples physical process state asynchronously so heartbeat/reconnect paths are not blocked by slow Windows process enumeration. Physical snapshots have their own freshness TTL and fail closed when stale or unresolved.

## 6. Bootstrap NON_GIT -> exact Git materialization

A registered path containing only `.remotemcp-bootstrap` is legitimately detected as `NON_GIT`. Earlier versions could bind that state but had no canonical transition to a Git checkout, leaving the project unable to use Git task/worktree semantics.

V3.1 provides an explicit materialization operation with the following invariants:

1. preserve the existing `project_id`, device binding, path and binding generation;
2. require current central and node project kind `NON_GIT`;
3. require zero existing project task state;
4. require the canonical root to contain no content other than the bootstrap marker;
5. require `remote_url`, `remote_ref` and an exact 40-character `expected_commit_sha`;
6. stage Git in a sibling directory;
7. fetch the requested ref and require `FETCH_HEAD == expected_commit_sha`;
8. check out the exact commit detached;
9. verify origin and HEAD before cut-over;
10. atomically replace the bootstrap root only after verification;
11. update node and central project kind to `GIT` only after the exact checkout is proven;
12. restore/preserve the bootstrap root on any pre-cutover or cut-over failure.

The operation is infrastructure state transition, not scientific execution.

## 7. Zero-C target

The V3.1 Windows production target reserves the OS drive for Windows itself.

The preferred target layout is conceptually:

```text
<NON_OS_REMOTEMCP_ROOT>/
  runtime/
  venv/
  logs/
  tmp/
  cache/
  control/
  source/
```

Research repositories, managed project workspaces and task worktrees also remain on approved non-OS storage.

Zero-C migration is a distinct operational step from source qualification and from a gateway/node code cut-over.

## 8. Rolling control-plane upgrade while a protected long run is active

When an important scientific process must continue, treat its OS identity as a separate safety contract even if the current RemoteMCP release reports zero active durable jobs.

### 8.1 Required protected-process record

Before cut-over record at least:

- exact PID;
- process creation/start identity where available;
- executable path;
- command line or workload identity;
- parent/child relationship when available;
- canonical output path;
- current source/venv/runtime paths used by the process or its durable worker.

Do not kill or replace that process tree during RemoteMCP maintenance.

### 8.2 Safe actions while it runs

The following are normally compatible with a protected long run when separately verified:

- merge/qualify source on GitHub;
- create a new immutable release directory;
- create a new virtual environment;
- run import/static/isolated startup probes;
- restart only the gateway/control-plane process if it does not own the protected payload;
- restart only the RemoteMCP node process if detached workers/payloads remain alive and their old runtime/source/venv paths are retained.

### 8.3 Deferred actions

Do not perform these while the protected process still depends on old paths:

- Windows reboot;
- process-tree termination;
- deletion or overwrite of the old release/venv;
- moving/deleting its durable runtime;
- Zero-C migration of a runtime still used by the long-running worker;
- cleanup/prune of its worktree or evidence path.

### 8.4 Split-brain rule

Never let an old worker continue writing one runtime while a new node assumes the same job has been migrated to another runtime.

If a protected worker still references the legacy runtime:

```text
old worker -> legacy runtime     KEEP
new node   -> either same runtime or no claim over that job
```

Full runtime migration waits until the protected worker is terminal.

## 9. Source qualification vs live state

Always report these separately:

```text
SOURCE QUALIFIED
SOURCE MERGED
GATEWAY DEPLOYED
NODE DEPLOYED
RUNTIME MIGRATED
ZERO-C COMPLETE
```

A source commit may be fully qualified while the live machine remains on an earlier release. Do not infer deployment from Git history.

## 10. Operational upgrade sequence

For a protected-long-run rolling upgrade:

1. freeze an exact qualified source commit;
2. verify the protected OS process is still alive and record its identity;
3. verify which old runtime/source/venv paths must remain;
4. stage the candidate release side-by-side;
5. run isolated gateway/node import and startup probes;
6. cut over gateway/control-plane source with rollback available;
7. cut over the node code only if doing so does not terminate the protected process tree;
8. verify same device identity/fingerprint/route generation;
9. verify fresh heartbeat and agent task submission;
10. verify the protected PID remains alive after cut-over;
11. leave legacy runtime/source/venv in place while the protected process runs;
12. after the protected process is terminal, perform the deferred Zero-C/runtime migration and cleanup.

## 11. Scientific boundary

Infrastructure recovery, upgrade, process attestation, project materialization and connector failures are not scientific PASS/FAIL outcomes.

Do not alter scientific lineage merely because a control-plane operation is blocked or recovered. Scientific evidence must continue to come from the preregistered scientific execution and independent QA path.
