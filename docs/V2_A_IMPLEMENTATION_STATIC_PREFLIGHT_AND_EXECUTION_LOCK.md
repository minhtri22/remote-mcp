# V2-A Implementation Static Preflight and Execution Lock

Status: **FROZEN / PASS**
Gate: `REMOTE_MCP_V2A_IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK`
Date: 2026-09-30

This gate is specification-only. It authorizes implementation only after the static validator passes. It does not itself add durable execution.

Bound prelock commit:

`6e4aa565aa6cc022b88f33f4d3ee8f076f6ecec7`

Machine-readable source of truth:

`specs/v2a_implementation_lock.json`

## 1. Exact implementation module layout

The V2-A runtime must be created under:

```text
remotemcp/
  __init__.py
  durable/
    __init__.py
    config.py
    errors.py
    db.py
    models.py
    operations.py
    events.py
    jobs.py
    process.py
    reconcile.py
    service.py
    worker.py
    migrations/
      001_v2a.sql
```

`server.py` remains the composition root only. The durable package must not depend on FastMCP decorators, `server.py`, or `oauth_provider.py`.

The first implementation migration must be byte-for-byte identical to `specs/v2a_schema_v1.sql`.

## 2. Migration/bootstrap path

Startup order is frozen:

```text
resolve MCP_RUNTIME_DIR
  -> create runtime dir
  -> open runtime.db
  -> SQLite pragmas
  -> BEGIN IMMEDIATE migration lock
  -> verify/apply schema v1 by checksum
  -> COMMIT
  -> ensure jobs/
  -> startup reconciliation
  -> start supervisor
  -> accept durable mutations
```

Checksum mismatch is startup-fatal. V2-A contains no destructive migration.

Only one active supervisor per runtime directory is an operational assumption in V2-A; active-active multi-server scheduling is not claimed.

## 3. Worker protocol

Durable payloads must not be children of the MCP request lifecycle.

The server launches only the RemoteMCP worker wrapper:

```text
python -m remotemcp.durable.worker
  --runtime-dir <absolute>
  --job-id <job_id>
  --launch-nonce <nonce>
```

User argv is not placed on the worker command line.

The handshake is:

```text
job DB row: QUEUED
      ↓
STARTING
      ↓
spawn worker
      ↓
worker.json: WAITING_FOR_COMMIT
      ↓
worker waits — NO PAYLOAD
      ↓
server verifies worker fingerprint
      ↓
DB launch commitment
      ↓
atomic launch.commit
      ↓
worker validates commit
      ↓
spawn payload shell=false
      ↓
worker.json: PAYLOAD_RUNNING
      ↓
stdout.log / stderr.log
      ↓
terminal.json
```

`LOST` is never emitted by the worker. It is only a reconciler classification.

## 4. Exact durable command contract

`job_submit` accepts `argv: list[str]`, not a shell command string.

The executable must match the owner-configured durable allowlist. Default durable allowlist equals the V2-0 ten-command allowlist. The owner may add executables such as Ollama/llama binaries using startup configuration `MCP_DURABLE_ALLOWED_CMDS`; callers cannot extend the allowlist.

`cwd` is a relative path resolved through the same workspace containment contract as baseline `safe()`.

No arbitrary caller environment is supported in V2-A.

## 5. Exact job_* signatures

```python
async def job_submit(
    operation_id: str,
    argv: list[str],
    cwd: str = ".",
    agent_id: str = "",
    project_id: str = "",
    task_id: str = "",
) -> dict

def job_get(job_id: str) -> dict

async def job_wait(
    job_id: str,
    subscriber_id: str,
    mode: str = "terminal",
    after_event_id: int = 0,
    timeout_seconds: int = 55,
) -> dict

def job_logs(
    job_id: str,
    stream: str = "stdout",
    cursor: int = 0,
    limit_bytes: int = 65536,
) -> dict

def job_result(
    job_id: str,
    subscriber_id: str = "",
    ack_event_id: int = 0,
) -> dict

async def job_cancel(
    operation_id: str,
    job_id: str,
    agent_id: str = "",
    project_id: str = "",
    task_id: str = "",
) -> dict
```

All return JSON-serializable dictionaries.

Important ACK rule: `job_wait` never implicitly ACKs. A terminal event is ACKed only through `job_result(..., subscriber_id=..., ack_event_id=...)`.

## 6. Execution model

One in-process supervisor coordinates DB state and detached workers.

Server shutdown stops the supervisor but does not kill verified durable payloads merely because the MCP server is exiting.

Default maximum parallel durable jobs is 2, configurable by `MCP_MAX_PARALLEL_JOBS`.

Queue order is deterministic:

`created_at_ms ASC, job_id ASC`.

## 7. Long-job semantics

The long-job path is never:

```text
MCP request -> wait 10 minutes -> process
```

It is:

```text
job_submit
  -> durable job_id immediately
  -> detached worker/process

job_wait
  -> bounded <=55 s observation window

job_result
  -> terminal result + optional explicit ACK
```

The V2-A qualification includes a **610-second** process to prove the process outlives the originating request.

## 8. Static implementation test matrix

The implementation must create the exact `tests/v2a/` suite named in the machine lock. It covers:

- DB bootstrap and migration checksum;
- idempotent operations and conflict semantics;
- event uniqueness and ACK behavior;
- repository state transitions;
- process ownership/PID reuse;
- worker pre-commit prohibition;
- all startup reconciliation cases;
- duplicate submit;
- cancellation races;
- restart recovery;
- safe Windows environment;
- legacy `run_command` compatibility;
- V2-0/OAuth/public regression.

Failure-injection points are also frozen in the lock.

## 9. Execution boundaries

Authorized after this lock passes:

- create the exact durable module layout;
- implement schema/bootstrap;
- add exact `job_*` tools;
- edit `server.py` only for service wiring/tool exposure;
- repair only the known legacy child-environment defect;
- add V2-A tests and qualification scripts.

Not authorized:

- OAuth changes;
- ROOT containment changes;
- changes to baseline file/search tool signatures;
- changes to legacy `run_command` public semantics beyond the child environment repair;
- V2-B leases/task registry/worktrees;
- V2-C context broker;
- MCP Tasks extension;
- schema/state-machine redesign;
- shell execution;
- full environment inheritance;
- auto-relaunch of LOST jobs.

If implementation discovers that schema/state-machine changes are necessary, implementation stops and a new prospective prelock is required.

## 10. Gate acceptance

Static preflight PASS requires:

1. JSON lock schema validates;
2. every planned module has one responsibility and forbidden dependency rules;
3. migration source/checksum binding is exact;
4. worker protocol contains a pre-payload durable launch barrier;
5. exact tool signatures are unique and bounded;
6. test matrix covers every prelock state/error invariant;
7. execution boundaries forbid V2-B/V2-C/V2-D scope;
8. runtime source remains unchanged at this gate;
9. V2-0 regression remains PASS.

On PASS, the exact next gate is:

`REMOTE_MCP_V2A_DURABILITY_IMPLEMENTATION_AND_ZERO_SCIENCE_QUALIFICATION`.
