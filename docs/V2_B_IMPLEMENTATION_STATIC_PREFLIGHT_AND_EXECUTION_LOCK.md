# V2-B Implementation Static Preflight and Execution Lock

Status: **FROZEN / AMENDED — LOCAL REVALIDATION PENDING**
Gate: `REMOTE_MCP_V2B_IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK`
Date: 2026-09-30

This lock authorizes V2-B implementation only after static validation. It does not implement V2-B runtime and does not touch the live port-8099 service.

Bound amended prelock:

`4dbbf0959112db1d4f442ef65e3ce08965ad3edc`

Machine source of truth:

`specs/v2b_implementation_lock.json`

## Exact module layout

```text
remotemcp/multiagent/
  __init__.py
  config.py
  principal.py
  tokens.py
  models.py
  agents.py
  projects.py
  tasks.py
  leases.py
  worktrees.py
  cas.py
  guard.py
  jobs.py
  reconcile.py
  service.py
  migrations/
    002_v2b.sql
```

The multi-agent package contains no FastMCP decorators. `server.py` remains the composition root.

## Migration 002

`002_v2b.sql` must be byte-identical to `specs/v2b_schema_v2.sql`.

The existing V2-A database bootstrap is generalized to ordered migrations:

```text
001_v2a.sql
  -> 002_v2b.sql
```

Each migration is SHA-256 checked in `schema_migrations`. There are no destructive drops/renames in V2-B.

## Owner account, OAuth client identity and replayable lease tokens

The owner account identity is **not** the OAuth client ID.

The control plane persists one stable:

```text
owner_account_id = own_...
```

in `<MCP_RUNTIME_DIR>/owner-account.json`.

The current request still obtains:

```python
mcp.server.auth.middleware.auth_context.get_access_token().client_id
```

but that value is only `auth_client_id` for session audit/isolation. Missing access token is `AUTH_REQUIRED`.

Two different OAuth clients from two login machines map to the same `owner_account_id` and may hold ACTIVE sessions concurrently. There is no last-login-wins routing.

Lease cleartext is never persisted. A runtime-local 32-byte HMAC key at:

`<MCP_RUNTIME_DIR>/lease-token.key`

derives:

```text
lt1_<nonce>_<HMAC(task_id|epoch|agent_id|session_id|nonce)>
```

The DB stores nonce + SHA-256(clear token). This lets a lost `task_claim` response be replayed with the same current token without writing the token to SQLite/logs.

## Multi-device release sequence

V2-B implementation must qualify concurrent login from at least two machines/OAuth clients under the same owner account.

Routing in V2-B is session-scoped:

```text
owner account
  ├── auth client A -> session A
  └── auth client B -> session B
```

The project/task namespace is shared at owner-account scope.

Routing to **multiple execution machines** is a separate mandatory successor before production release:

`REMOTE_MCP_V2BD_MULTI_DEVICE_ROUTING_PRELOCK`

That gate will freeze `device_id`, project→device binding, task device inheritance, node authentication/transport and offline behavior. V2-B must not guess or partially implement the distributed node transport.

## Claim transaction boundary

Claim is deliberately two-phase because Git is external to SQLite:

```text
BEGIN IMMEDIATE
  capacity check
  READY/RECOVERABLE -> CLAIMED
  epoch++
  create task lease
  persist token nonce/hash
COMMIT
      ↓
deterministic worktree create/recover
      ↓
BEGIN IMMEDIATE
  revalidate same lease
  validate worktree identity
  CLAIMED -> RUNNING
COMMIT
      ↓
operation success without token
      ↓
derive token and return
```

A crash at any boundary is reconciled from the committed CLAIMED/lease/task metadata and deterministic branch/path. No second claimant can win while the current CLAIMED lease is valid.

## Worktree recovery

Path:

`.remote-worktrees/<project_id>/<task_id>`

Branch:

`remotemcp/task/<task_id>`

No merge/rebase/reset is performed automatically.

Recovery inspects `git worktree list --porcelain` and recorded branch/path:

- expected path+branch => reuse;
- branch exists but not attached => attach existing task branch;
- unexpected directory or branch elsewhere => preserve and report conflict;
- dirty/ambiguous terminal worktree => preserve with `cleanup_pending=1`;
- clean terminal worktree with no running jobs may be removed, but the branch is preserved.

## CAS transaction boundary

V2-B introduces `cas_mutations` because an operation request hash alone cannot prove whether atomic replacement already happened.

```text
operation reserved
  ↓
PREPARED intent committed
  ↓
temp write + fsync
  ↓
recheck expected current hash
  ↓
atomic replace
  ↓
REPLACED
  ↓
verify after hash
  ↓
COMMITTED + operation result
```

Startup reconciliation can determine whether replacement already occurred by comparing current hash to expected/intended hashes. It never blindly performs a second write.

If the lease expires before replacement, no replacement is performed. If replacement already happened, reconciliation may finalize the already-applied operation without requiring the expired lease to become current again.

## Managed-mode legacy guard

Once any V2-B project is registered:

- `run_command` => `TASK_CONTEXT_REQUIRED`;
- direct V2-A `job_submit` => `TASK_CONTEXT_REQUIRED`;
- direct `job_cancel` is rejected for task-bound jobs;
- legacy `write_file/edit_file` are rejected for registered project roots and task worktrees;
- read tools remain compatible.

This is necessary because argv path arguments can bypass a file-only guard.

## Task-bound durable jobs

`task_job_submit` validates the current lease then resolves the task execution root.

For Git projects, the execution root is the task worktree.

For non-Git projects, process execution additionally requires a `TREE WRITE_EXCLUSIVE` path lease on project root `.`.

The wrapper calls the existing V2-A DurableService internally with the same operation ID and binds `agent_id/project_id/task_id`.

Lease expiry never kills a launched durable job.

## Full implementation matrix

The exact test files and qualification scripts are frozen in the machine lock.

Required high-value gates include:

- 3 agents / 2 projects;
- 2 simultaneous worktrees in one Git project;
- competing claim exactly-one-winner;
- token replay without cleartext persistence;
- lease expiry/takeover with stale-token rejection;
- durable job survival across takeover;
- CAS two-writer race exactly-one-winner;
- CAS crash recovery around replace/result windows;
- worktree crash recovery without reset/prune/data loss;
- legacy bypass rejection;
- all V2-A/V2-0/OAuth/public regressions remain PASS;
- live port 8099 identity remains unchanged.

## Deployment boundary

This lock explicitly forbids restarting/repointing/replacing the active RemoteMCP service. Source development and isolated QA continue separately until the later release-QA gate and explicit owner approval.

## Observability

The previously recorded `/ops` agent-grouped read-only observability successor remains deferred. It is not part of this implementation lock.

## Next gate

On static PASS:

`REMOTE_MCP_V2B_MULTI_AGENT_IMPLEMENTATION_AND_ZERO_SCIENCE_QUALIFICATION`
