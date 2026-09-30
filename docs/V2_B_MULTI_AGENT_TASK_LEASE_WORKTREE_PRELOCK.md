# REMOTE_MCP_V2B_MULTI_AGENT_TASK_LEASE_WORKTREE_PRELOCK

Status: **FROZEN / PASS**
Date: 2026-09-30
Scope: specification only. No V2-B runtime implementation and no production deployment.

Bound V2-A closure:

- Git HEAD: `cdc3c3b41aa52cffc94865548d2e77ac404186b5`
- V2-A qualification manifest blob: `182a9b496e8c3c7e1aef485110a05bd1c107a5cb`

Machine-readable source of truth:

- `specs/v2b_prelock.json`
- `specs/v2b_schema_v2.sql`

## Deployment invariant

The currently active RemoteMCP on port 8099 / `remote.threadon.xyz` is **not** replaced, restarted or repointed during this prelock or V2-B implementation work.

The redesign is deployed only after the remaining planned phases are complete, an explicit release-QA gate passes, and the owner explicitly authorizes the switch. This protects the other research projects currently using the live remote.

## Identity model

`principal_key` comes from authenticated server context and is never caller supplied.

A logical agent has:

- stable server-generated `agent_id`;
- caller-generated opaque `client_instance_id`, unique under one principal;
- human `agent_name`;
- zero or more sessions.

Same principal + client instance resolves the same agent ID. Each registration creates a fresh `session_id`.

A task lease belongs to exactly one session.

## Task state machine

```text
CREATED -> READY -> CLAIMED -> RUNNING
                    |          |  |  |  \
                    |          |  |  |   -> CANCELLED
                    |          |  |  -> FAILED
                    |          |  -> COMPLETED
                    |          -> BLOCKED
                    -> RECOVERABLE
RUNNING -----------------------> RECOVERABLE
BLOCKED -> READY / FAILED / CANCELLED
RECOVERABLE -> CLAIMED / FAILED / CANCELLED
```

Terminal:

- COMPLETED
- FAILED
- CANCELLED

`BLOCKED` and `RECOVERABLE` hold no active task lease.

Completion requires a current lease, no running bound durable job, and a clean/committed Git task worktree.

## Heartbeat and leases

Defaults:

```text
heartbeat interval = 30 s
task lease TTL     = 120 s
allowed TTL range  = 60..600 s
```

Every claim increments `lease_epoch` and returns a new random `lease_token`. Only the SHA-256 of the token is stored.

All task-scoped mutation calls require the current token + epoch.

When a lease expires:

```text
RUNNING / CLAIMED
       ↓
RECOVERABLE
```

The old token can never become valid again. A new claimant receives a new epoch/token.

Heartbeat is the only V2-B mutating call that does not use `operation_id`; it uses a strictly monotonic `heartbeat_seq` to avoid unbounded operations-table growth.

## Project concurrency

Default: 4 active tasks per project, configurable 1..32.

Active capacity counts:

- CLAIMED
- RUNNING

It does not count:

- BLOCKED
- RECOVERABLE

Git projects gain concurrency through one isolated worktree per task. Non-Git projects gain mutation concurrency only through non-overlapping path leases.

No automatic merge/convergence to main is performed by V2-B.

## Git worktree contract

Task worktrees live at:

```text
.remote-worktrees/<project_id>/<task_id>
```

Task branch:

```text
remotemcp/task/<task_id>
```

Task creation resolves an exact base commit before any claim.

First claim creates the branch/worktree. Takeover reuses the same branch/worktree after identity validation.

Task mutation never targets the project's main working tree.

Cleanup is allowed only when:

- task is terminal;
- no non-terminal job is bound to it;
- worktree is clean;
- branch/worktree metadata matches the task.

Dirty or ambiguous worktrees are preserved for manual review. `git worktree prune` is never used blindly.

## Non-Git lease contract

Reads do not need a path lease.

Mutations require:

- current task lease;
- WRITE_EXCLUSIVE path lease.

Scopes:

- FILE
- TREE

TREE conflicts hierarchically with overlapping ancestors/descendants. FILE conflicts with the same file and ancestor TREE leases.

Expired leases are not transferred during takeover; the new session must acquire new path leases.

## CAS mutation contract

Inside a registered V2-B project, managed mutation uses CAS tools.

Legacy `write_file/edit_file` remain compatible outside registered projects, but are rejected with `LEASE_REQUIRED_USE_CAS` for paths covered by a managed V2-B project. This closes the multi-agent bypass path.

CAS hash:

`SHA-256(exact current file bytes)`

Allowed expected values:

- exact lowercase 64-hex hash;
- `MISSING` for create-if-absent.

No wildcard is permitted.

Write flow:

```text
validate lease token+epoch
  -> resolve task workspace / non-Git path lease
  -> read before hash
  -> compare expected
  -> unique temp file
  -> flush/fsync
  -> atomic replace
  -> verify after hash
  -> persist idempotent operation result
```

A mismatch produces zero write.

V2-B CAS content is UTF-8 text. Binary mutation is deferred.

## Durable V2-A jobs under multi-agent control

A launched V2-A job is not tied to agent heartbeat.

If the agent disappears:

```text
agent/session stale
task lease expires
task -> RECOVERABLE
job -> CONTINUES
worktree -> PRESERVED
```

A new claimant can inspect the same bound job and may cancel it only through task-scoped cancellation using the current lease.

A stale claimant cannot cancel it.

A task cannot transition COMPLETED while a bound job is non-terminal.

Direct V2-A `job_submit/job_cancel` are rejected for managed project/task execution; task-scoped wrappers enforce binding to the task workspace.

## Exact V2-B tools

The exact signatures are frozen in `specs/v2b_prelock.json`.

They include:

- agent registration/heartbeat/session close;
- project register/status;
- task create/claim/status/checkpoint/block/set-ready/release/complete;
- non-Git path lease acquire/release;
- file write/edit CAS;
- task-bound durable job submit/list/cancel.

All semantic mutations require `operation_id` except heartbeat, which uses `heartbeat_seq`.

## Multi-agent qualification matrix

The implementation must cover:

- identity/reconnect;
- competing claims;
- lease expiry/takeover/stale tokens;
- project concurrency;
- Git worktree create/reuse/dirty cleanup protection;
- non-Git hierarchical path conflicts;
- concurrent CAS races;
- agent loss while durable jobs continue;
- stale vs current claimant cancellation;
- crash windows around claim/worktree/CAS;
- at least 3 agents across at least 2 projects, with 2 concurrent tasks in one Git project.

## Observability answer / successor

**Yes — an agent-grouped log/status view is worth adding after V2-B identity/task semantics exist.**

The recommended successor is:

`REMOTE_MCP_OBSERVABILITY_AGENT_GROUPED_VIEW_PRELOCK`

Proposed shape:

```text
/ops
  Project
    -> Task
      -> Agent
        -> Session
          -> Job
             status / timing / bounded logs / events
```

It should be read-only first, owner-authenticated, redact secrets/lease tokens, consume the same SQLite/event journal, and never execute commands from the dashboard initially.

This is intentionally **not implemented in V2-B prelock**. After V2-B, the system will finally have stable agent/task IDs, so grouping logs by agent becomes meaningful and cheap in context terms.

## Explicitly forbidden in this prelock

- changing/restarting production port 8099;
- creating V2-B runtime tables/modules;
- creating/deleting real Git worktrees;
- changing V2-A durable-job semantics;
- implementing the observability UI;
- implementing V2-C Context Broker;
- implementing native MCP Tasks.

## Next gate

After static validation PASS:

`REMOTE_MCP_V2B_IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK`
