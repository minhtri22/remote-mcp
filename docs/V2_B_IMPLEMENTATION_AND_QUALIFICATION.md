# V2-B Multi-Agent Implementation and Zero-Science Qualification

Status: **PASS / FORMALLY CLOSED**
Date: 2026-09-30
Gate: `REMOTE_MCP_V2B_MULTI_AGENT_IMPLEMENTATION_AND_ZERO_SCIENCE_QUALIFICATION`

Production deployment: **NOT PERFORMED**

The active production RemoteMCP remained on:

- `127.0.0.1:8099`
- PID `28464`
- command `python.exe .\server.py`

through the final QA cycle.

## Delivered

### Multi-login owner identity

- stable `owner_account_id` independent from OAuth DCR `client_id`;
- concurrent ACTIVE sessions from multiple login machines;
- `auth_client_id` stored per session for audit/isolation;
- no last-login-wins behavior;
- fail-closed startup if `owner-account.json` disappears while persisted owner state exists.

### Agent/session/task/lease runtime

- agent/session registration and heartbeat sequence;
- task states and prospective transitions;
- task lease epoch + HMAC-derived token replay;
- no clear lease-token persistence;
- expiry to `RECOVERABLE`;
- takeover invalidates old epoch/token;
- project active-task concurrency enforcement.

### Git worktree isolation

Each Git task uses:

```text
.remote-worktrees/<project_id>/<task_id>
remotemcp/task/<task_id>
```

Recovery uses deterministic branch/path identity and `git worktree list --porcelain`.

No automatic merge/rebase/reset/prune of user history.

### Non-Git leases

- FILE/TREE scopes;
- WRITE_EXCLUSIVE mutation leases;
- hierarchical conflict detection;
- process jobs on NON_GIT require project-root TREE lease.

### CAS mutation journal

```text
PREPARED -> REPLACED -> COMMITTED
     \----------------> ABORTED
```

CAS stores expected-before and intended-after hashes and reconciles crashes around atomic replacement without blindly writing twice.

Concurrent writes from the same expected hash allow exactly one commit.

### Managed-mode guards

Once any V2-B project is registered:

- legacy `run_command` is rejected;
- direct V2-A `job_submit` is rejected;
- direct `job_cancel` is rejected for task-bound jobs;
- legacy `write_file/edit_file` are rejected inside registered project roots/task worktrees;
- reads remain compatible.

Managed execution uses task-scoped jobs and CAS mutation tools.

### Durable-job interaction

Lease/session loss does not kill a launched V2-A durable job.

Takeover behavior qualified:

- old task becomes RECOVERABLE;
- durable job continues;
- new claimant receives new epoch/token;
- stale token cannot cancel;
- new claimant sees the existing job;
- current claimant can cancel task-bound jobs.

## Qualification evidence

### Test suites

```text
V2-B implementation suite: 24 passed
V2-A regression:           27 passed
V2-0 regression:           17 passed
```

### OAuth / public endpoint regression

PASS:

- OAuth AS metadata;
- protected-resource metadata;
- DCR;
- PKCE;
- token exchange;
- refresh rotation/revoke;
- lockout;
- MCP initialize;
- 32-tool surface;
- legacy path traversal/allowlist protections;
- public `/mcp` unauthenticated 401 Bearer challenge.

### Three agents / two projects

**PASS**

- 3 agents;
- 2 projects;
- 3 OAuth-client sessions under one owner account;
- 2 concurrent tasks in one Git project;
- distinct worktrees.

### Competing claim

**PASS**

- exactly one winner;
- exactly one active task lease;
- loser rejected with task-state conflict.

### CAS race

**PASS**

- two writers from the same expected hash;
- exactly 1 COMMITTED;
- exactly 1 `CAS_MISMATCH`;
- final file equals one complete candidate, never a mixed write.

### Worktree crash recovery

**PASS**

- crash after DB claim before worktree create recovered;
- crash after worktree create before RUNNING finalize recovered;
- no duplicate worktree.

### Durable-job takeover

**PASS**

- job survived lease expiry and completed `SUCCEEDED`;
- old token rejected with `LEASE_STALE`;
- takeover epoch incremented from 1 to 2;
- current claimant cancellation produced terminal `CANCELLED`.

### Static implementation validation

PASS:

- exact V2-B module layout;
- migration 002 byte identity;
- no FastMCP decorators inside multiagent package;
- no `shell=True` / `os.system`;
- no destructive Git recovery commands;
- managed-mode guards wired in `server.py`;
- V2-A bootstrap remains schema-v1 compatible;
- V2-B explicitly upgrades 001 -> 002;
- owner-account/HMAC key bootstrap;
- exact 20 V2-B signatures;
- production PID 28464 unchanged.

## Known boundaries

V2-B does **not** yet provide routing to multiple execution machines.

It supports multiple login devices/sessions controlling the same execution node.

Multiple RemoteMCP execution machines are the mandatory next phase:

`REMOTE_MCP_V2BD_MULTI_DEVICE_ROUTING_PRELOCK`

V2-BD must freeze:

- stable `device_id`;
- owner/device pairing;
- project -> device binding;
- task device inheritance;
- single public endpoint routing;
- node authentication/transport replay protection;
- device heartbeat/offline/revoked semantics;
- no silent cross-device task migration.

Also still deferred:

- Context Broker;
- native MCP Tasks extension;
- read-only `/ops` observability UI;
- production deployment/replacement of port 8099.

## Verdict

**V2-B PASS.**

The exact next authorized gate is:

`REMOTE_MCP_V2BD_MULTI_DEVICE_ROUTING_PRELOCK`