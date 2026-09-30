# REMOTE_MCP_V2A_DURABLE_JOB_AND_OPERATION_PRELOCK

Status: **FROZEN / PASS**
Date: 2026-09-30
Scope: specification only. No durable runtime implementation is authorized by this document.

Baseline bound to:

- Git HEAD: `f1ed1172a9590c69baef1864eb536c277607029f`
- `server.py` blob: `833b8f1f7075bd2abf9ddc4de94c0bb40ee9b788`
- `oauth_provider.py` blob: `491cf36a535be64bb70e672aed410d50618f1494`
- V2-0 freeze blob: `59ef042b50e62b66d4b00e69e5fb9e22e4c2812f`

Machine-readable source of truth: `specs/v2a_prelock.json`.

## 1. SQLite schema v1

Runtime state will live under:

```text
MCP_RUNTIME_DIR
default: ~/.remotemcp

~/.remotemcp/
  runtime.db
  jobs/
    <server-generated-job-id>/
      stdout.log
      stderr.log
      worker.json
      launch.commit
      terminal.json
      result.json
```

SQLite contract:

- schema version: 1
- WAL mode
- `foreign_keys=ON`
- `busy_timeout=5000`
- `synchronous=NORMAL`
- short transactions only
- no network filesystem requirement in v1

The frozen DDL is in `specs/v2a_schema_v1.sql`.

Tables:

- `schema_migrations`
- `operations`
- `jobs`
- `events`
- `event_cursors`

No agents/tasks/projects tables are introduced in V2-A. Their identifiers are nullable opaque context fields until V2-B owns those entities.

## 2. Operation idempotency state machine

States:

```text
RESERVED
   |
   v
EXECUTING
 |   |   |   \
 |   |   |    -> IN_DOUBT
 |   |   -> FAILED_FINAL
 |   -> FAILED_RETRYABLE --+
 -> SUCCEEDED              |
                           +--> EXECUTING
```

Rules:

1. `operation_id` is globally unique.
2. The first request stores a deterministic `request_hash`.
3. Reuse of the same `operation_id` with a different `request_hash` returns `OPERATION_CONFLICT`; the existing row is never mutated.
4. Same ID + same hash:
   - RESERVED/EXECUTING -> `IN_PROGRESS`
   - SUCCEEDED -> replay persisted result
   - FAILED_RETRYABLE -> may execute again with the **same** operation ID
   - FAILED_FINAL -> replay persisted failure
   - IN_DOUBT -> reconcile only; never execute blindly
5. A retry changes `attempt_count`, not `request_hash`.
6. `IN_DOUBT` has no automatic transition back to EXECUTING.

Canonical request hash:

```text
SHA-256(
  canonical-json({
    kind,
    normalized_arguments,
    principal_key,
    agent_id,
    project_id,
    task_id
  })
)
```

Canonical JSON is UTF-8, keys sorted, separators `(',', ':')`, no insignificant whitespace. Attempt number, timestamps and transport request IDs are excluded.

This contract gives exactly-once **execution intent** for a mutation/job submission when callers reuse `operation_id`; it does not claim exactly-once network delivery.

## 3. Job state machine

```text
QUEUED
  | \
  |  -> CANCELLED
  v
STARTING
  |  \------> FAILED
  |   \-----> LOST
  |    \----> CANCELLING
  v
RUNNING
 |  |  |  \
 |  |  |   -> LOST
 |  |  -> FAILED
 |  -> SUCCEEDED
 -> CANCELLING
        |  |  |  \
        |  |  |   -> LOST
        |  |  -> FAILED
        |  -> SUCCEEDED
        -> CANCELLED
```

Terminal states:

- SUCCEEDED
- FAILED
- CANCELLED
- LOST

A LOST job is never relaunched automatically. A new execution requires a new explicit submission/operation according to reconciliation policy.

Exactly one `launch_nonce` is bound to a job.

## 4. Process ownership and fingerprint

V2-A requires a RemoteMCP worker wrapper. The payload is not spawned directly by the MCP request handler.

Required fingerprint fields:

```text
pid
start_token
executable_canonical
command_sha256
```

Windows v1 `start_token` is the decimal Win32 process creation FILETIME.

Ownership rule:

> RemoteMCP may signal/cancel a process only when PID + start token match the persisted fingerprint. Executable path and command hash must also match when observable.

If identity cannot be proven, RemoteMCP **must not signal the PID**. Reconciliation classifies the job LOST.

Launch handshake:

1. job is durably QUEUED with server-generated `job_id` + `launch_nonce`;
2. supervisor moves it to STARTING;
3. worker wrapper starts with `job_id` + `launch_nonce`;
4. worker atomically publishes `worker.json` containing its fingerprint;
5. worker waits and **does not start the payload**;
6. supervisor verifies the fingerprint and persists launch commitment;
7. `launch.commit` is atomically published;
8. only then may the worker start the payload.

This closes the crash window where a payload could exist but its PID was never durably bound.

## 5. Log/result filesystem contract

Job directory:

```text
<MCP_RUNTIME_DIR>/jobs/<job_id>/
```

`job_id` is server generated; caller-controlled path components are forbidden.

Files:

- `stdout.log`: raw append-only bytes
- `stderr.log`: raw append-only bytes
- `worker.json`: atomic worker/payload handshake metadata
- `launch.commit`: atomic durable launch commitment
- `terminal.json`: atomic worker terminal marker
- `result.json`: atomic normalized result metadata

`job_logs` decodes log bytes as UTF-8 with replacement. JSON metadata is UTF-8.

Metadata writes use temp file + flush/fsync where supported + atomic replace.

Secrets/environment values are not written to metadata by default.

## 6. Terminal event cursor / ACK semantics

Exactly one terminal event row exists per job, enforced by the partial unique index on `events(job_id)` where `terminal=1`.

Delivery guarantee is intentionally:

> **at-least-once until ACK; no redelivery to the same subscriber after ACK**

It is not called exactly-once delivery.

Cursor key:

```text
(subscriber_id, job_id)
```

`ack_event_id` is monotonic. It can only increase.

If the network response carrying a terminal event is lost, the event may be returned again because it was not ACKed. The job result/process is never rerun.

Fallback wait modes:

- `terminal`
- `heartbeat`
- `progress`

Fallback long-poll is bounded to 55 seconds. A timeout with no new event returns a compact no-change/running response, not log history.

V2-A does not send unsolicited server-to-client messages. Native MCP Tasks mapping is deferred to V2-D.

## 7. Retryable vs non-retryable errors

Transport retry is owned by the client/gateway. RemoteMCP's responsibility is to make retries safe and classify errors.

Never retry:

- INVALID_ARGUMENT
- FORBIDDEN
- NOT_FOUND
- PATH_ESCAPE
- COMMAND_NOT_ALLOWED
- CAS_MISMATCH
- OPERATION_CONFLICT
- SPAWN_INVALID_EXECUTABLE

Reauthenticate once, then retry:

- AUTH_REQUIRED

Safe for reads, or mutations only with the same `operation_id`:

- RATE_LIMITED
- TRANSPORT_TIMEOUT
- UPSTREAM_502
- UPSTREAM_503
- UPSTREAM_504
- DB_BUSY

Same-operation retry is allowed only when failure proves no child/side effect was created:

- SPAWN_RESOURCE_EXHAUSTED_BEFORE_CHILD

Reconcile only, never blindly retry:

- OPERATION_IN_DOUBT
- PROCESS_OWNERSHIP_MISMATCH

Default retry envelope for eligible errors:

```text
max attempts: 5
base: 250 ms
max delay: 8000 ms
jitter: full
```

## 8. Startup reconciliation rules

### Operations

- RESERVED: if there is no durable side-effect marker, move to FAILED_RETRYABLE; otherwise reconcile by operation kind.
- EXECUTING: never re-execute blindly. Reconcile evidence; if the outcome cannot be proven, move to IN_DOUBT.
- FAILED_RETRYABLE: remains retryable with the same ID/hash.
- SUCCEEDED: no action.
- FAILED_FINAL: no action.
- IN_DOUBT: no automatic execution.

### Jobs

QUEUED:
- remain QUEUED and eligible for normal supervisor start.

STARTING:
- validate `job_id + launch_nonce` worker handshake;
- valid matching worker -> persist/restore launch commitment and continue;
- no valid identity after 30 s grace -> LOST;
- never spawn a second worker for the same launch nonce.

RUNNING:
- valid worker fingerprint -> keep RUNNING;
- valid atomic `terminal.json` -> terminalize from the marker;
- otherwise -> LOST.

CANCELLING:
- valid worker -> continue cancellation;
- valid terminal marker -> terminalize according to marker;
- otherwise -> LOST.

Terminal jobs:
- never change terminal state;
- ensure the single terminal event exists; insert transactionally only when absent.

Conservative rule:

> When state cannot be proven, prefer LOST/IN_DOUBT over duplicate execution or signaling an unowned PID.

## 9. Backward compatibility contract for run_command

Public surface remains:

```python
run_command(command: str) -> str
```

Must preserve:

- tool name and string input
- `shlex.split`
- same 10-command allowlist
- `cwd=ROOT`
- stderr merged into stdout
- `MAX_OUT=20000`
- 60-second compatibility timeout
- success prefix `[exit <code>]\n`
- rejection wording begins `Từ chối:`
- timeout wording `Timeout sau 60s`

V2-0 froze the PATH+HOME-only child environment as a defect. V2-A is explicitly authorized to repair only this part using a minimal allowlist; full server environment inheritance remains forbidden.

Frozen Windows environment keys:

```text
PATH
HOME          (forced to ROOT)
SystemRoot
WINDIR
USERPROFILE
TEMP
TMP
COMSPEC
PATHEXT
SYSTEMDRIVE
```

Durable `job_*` tools are separate. `run_command` is not silently converted into an unlimited long-running call.

## 10. Explicitly forbidden in this prelock

This step does **not** implement:

- SQLite runtime repositories
- migrations executor
- job worker/supervisor
- any `job_*` MCP tool
- `file_edit_cas`
- startup reconciliation code
- runtime retry loops
- native MCP Tasks extension

## 11. Prelock acceptance

PASS requires:

1. machine spec validates structurally;
2. DDL parses in an in-memory SQLite database;
3. all states/transitions are internally closed;
4. all terminal job states have no outgoing transitions;
5. exactly one terminal event can be inserted per job under schema constraints;
6. baseline hashes match the frozen V2-0 repository;
7. no runtime source file is changed by this prelock.

When PASS, next authorized gate is:

`REMOTE_MCP_V2A_IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK`.
