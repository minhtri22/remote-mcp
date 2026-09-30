# RemoteMCP v2 — Durable Multi-Agent Runtime Implementation Plan

Status: ACTIVE
Current gate: REMOTE_MCP_V2B_MULTI_AGENT_IMPLEMENTATION_AND_ZERO_SCIENCE_QUALIFICATION
V2-0 status: PASS (2026-09-30)
V2-A status: PASS (2026-09-30)
Repository: minhtri22/remote-mcp
Local source: D:\WORK\RESEARCH\RemoteMCP-src

## 1. Goal

Evolve RemoteMCP from a single-request filesystem/command MCP server into a durable local control plane for many agents, many projects, large contexts, and long-running jobs.

Target properties:

- survive browser/client disconnects and OAuth reconnects;
- never blindly replay mutating operations;
- allow multiple agents to work concurrently without corrupting a shared workspace;
- allow one owner account to stay logged in on multiple client devices concurrently, and require a later multi-execution-device routing phase before production release;
- isolate Git work by task/worktree;
- persist task/job/event state in SQLite WAL;
- support long-running llama/Ollama/pytest/research jobs without holding one HTTP request open;
- compact context so an agent can resume a project without rereading the whole repository;
- preserve a small, auditable MCP surface with explicit safe semantics.

## 2. Baseline

Current server exposes:

- list_dir
- read_file
- write_file
- edit_file
- search
- run_command

Current limitations:

- run_command is request-bound and has CMD_TIMEOUT=60 seconds;
- timeout kills the child process;
- no durable operation identity;
- no retry semantics for mutations;
- no durable jobs;
- no agent/session/task identity;
- no leases or worktree isolation;
- no durable journal/checkpoints;
- no context manifest/resume package.

The OAuth implementation and ROOT path containment remain part of the trusted baseline.

## 3. Target architecture

    ChatGPT / Agent A    Agent B    Codex    Local Agent
             \             |        |          /
                      MCP Gateway
                 auth / routing / retry
                          |
                      Coordinator
        agent sessions / tasks / leases / checkpoints
                          |
        +-----------------+------------------+
        |                 |                  |
      Project A         Project B          Project C
       task queue        task queue         task queue
        |                 |                  |
      worker            worker             worker
        +-----------------+------------------+
                          |
                  D:\WORK\RESEARCH
                   files / git / jobs

Persistent control state:

    SQLite WAL
      agents
      sessions
      projects
      tasks
      operations
      jobs
      leases
      checkpoints
      events

Filesystem state:

    .remotemcp/
      logs/
      artifacts/
      worktrees/
      job-output/

## 4. Design invariants

### 4.1 Mutation idempotency

Every mutating request MUST carry:

- operation_id
- agent_id
- project_id
- task_id
- attempt
- expected_state_hash when a target state is known

An operation_id is globally unique.

If the same mutation is retried:

- never execute it twice;
- return the persisted outcome if already completed;
- return IN_PROGRESS if the first attempt is still active;
- return CONFLICT if payload differs from the original operation.

### 4.2 CAS for file mutations

file_edit_cas(path, expected_hash, patch/replacement, operation_id)

Rules:

- hash target before mutation;
- reject if expected_hash does not match;
- write atomically through a temporary file + replace;
- persist result_hash;
- replay of operation_id returns the same result without writing again.

### 4.3 Reads are retryable

Retry transient reads/status calls on:

- timeout
- 429 honoring Retry-After
- 502
- 503
- 504

Default:

- max attempts: 5
- exponential backoff
- jitter
- bounded total retry budget

Do not retry 400/403/404 automatically.

### 4.4 Circuit breaker

Per origin/service:

- open after 5 consecutive transient failures;
- cool-down 30 seconds;
- half-open with one probe;
- close after successful probe.

## 5. Long-running jobs

Long-running processes are first-class durable jobs.

Do NOT implement:

    run_command()
      -> keep HTTP request alive for minutes/hours
      -> kill process when request times out

Implement:

    job_submit()
      -> persist job
      -> spawn detached/supervised process
      -> return job_id immediately

    job_get(job_id)
    job_logs(job_id, cursor)
    job_result(job_id)
    job_cancel(job_id)

State machine:

    QUEUED
      -> STARTING
      -> RUNNING
      -> SUCCEEDED
       | FAILED
       | CANCELLED
       | LOST

Persist at minimum:

- job_id
- operation_id
- agent_id/session_id/project_id/task_id
- command spec
- cwd/worktree
- pid
- process creation fingerprint
- created_at/started_at/finished_at
- exit_code
- status
- stdout/stderr paths
- last_output_at
- last_heartbeat_at
- result metadata

### 5.1 Process survival

The job process must not be tied to the MCP HTTP request lifecycle.

The server supervises it independently and reconciles state after RemoteMCP restart.

On startup:

- inspect jobs persisted as STARTING/RUNNING;
- validate PID + process fingerprint;
- restore RUNNING when the same process still exists;
- otherwise classify LOST and preserve logs/artifacts.

### 5.2 Agent waiting modes

Support three observation modes.

#### Mode A — completion-only

Preferred for long one-shot inference/evaluation.

    job_wait(job_id, mode="terminal", timeout_seconds=N)

Behavior:

- wait until terminal state or bounded long-poll timeout;
- do not emit periodic text if nothing changed;
- terminal result has a monotonic terminal_event_id;
- terminal event is acknowledged once per subscriber/session.

This gives the agent effectively one meaningful notification when the process completes, while still respecting transport time limits.

#### Mode B — heartbeat

    job_wait(job_id, mode="heartbeat", interval_hint=60, timeout_seconds=N)

Return only compact liveness information:

- RUNNING
- elapsed
- last_output_at
- optional progress
- no large logs

Useful when the agent/user explicitly wants reassurance that a llama/Ollama run is still alive.

#### Mode C — progress

If the worker can parse progress:

- completed/total;
- current phase;
- ETA only when based on observed rate;
- latest checkpoint.

Progress is optional and never required for job correctness.

### 5.3 MCP Tasks compatibility

The internal Job abstraction MUST map cleanly onto the MCP Tasks extension model:

- durable task/job handle;
- status retrieval;
- cancellation;
- deferred result retrieval;
- survival across disconnects.

When the connected client negotiates the official Tasks extension, expose native task semantics.

When it does not, expose the fallback job_* tools above.

Do not make the persistence layer depend on one MCP SDK version.

## 6. Multi-agent and multi-device model

Account identity is distinct from OAuth client identity.

One stable `owner_account_id` owns the control-plane namespace. Different OAuth DCR clients from two login machines may keep concurrent ACTIVE sessions under that owner; routing is by explicit `session_id`, never by last login.

V2-B covers this multi-login-device requirement.

A separate mandatory pre-release phase, **V2-BD — Multi-device execution routing**, will cover multiple RemoteMCP execution machines under the same owner account. Projects will bind to a stable `device_id`; tasks inherit the project device; no non-terminal task silently migrates between devices.


Every call that participates in durable work is associated with:

- agent_id
- session_id
- project_id
- task_id

### 6.1 Agent lifecycle

Tools/internal API:

- agent_register
- agent_heartbeat
- agent_status

Defaults:

- heartbeat every 30 s when actively holding a lease;
- lease TTL 120 s;
- configurable per task class.

Loss of heartbeat does not kill a durable job.

### 6.2 Task lifecycle

State machine:

    CREATED
      -> READY
      -> CLAIMED
      -> RUNNING
      -> BLOCKED
      -> COMPLETED
       | FAILED
       | CANCELLED
       | RECOVERABLE

Tools:

- task_create
- task_claim
- task_status
- task_checkpoint
- task_resume
- task_release

A dead agent causes an expired task lease to become RECOVERABLE, not silently abandoned.

### 6.3 Git isolation

Never allow two agents to mutate the same Git working tree concurrently.

Preferred layout:

    <repo>
    .remote-worktrees/
      <task-id>--<agent-id>/

Task claim may allocate a dedicated git worktree and task branch.

Lifecycle:

    claim
      -> worktree create
      -> edit/test
      -> checkpoint/commit
      -> QA
      -> merge/converge decision
      -> release
      -> worktree cleanup

Non-Git directories use explicit path/file leases.

## 7. Journal and checkpoints

The event journal is the durable source of truth for resume, not the chat transcript.

Append events such as:

- AGENT_REGISTERED
- SESSION_STARTED
- TASK_CREATED
- TASK_CLAIMED
- OPERATION_STARTED
- OPERATION_COMPLETED
- JOB_SUBMITTED
- JOB_STARTED
- JOB_HEARTBEAT
- JOB_TERMINAL
- CHECKPOINT_CREATED
- TASK_RELEASED

Events are immutable append-only records.

A checkpoint records:

- task state
- last completed step
- next authorized step
- relevant commit/hash
- modified files
- running job IDs
- result/artifact refs
- concise human/agent summary

resume_task(task_id) returns the latest checkpoint + only the journal tail required after that checkpoint.

## 8. Context Broker

Goal: resume projects with a compact context package instead of rereading the repository.

project_open(project_id) should return a manifest containing:

- project root
- current branch/worktree
- active tasks
- important files and hashes
- dirty files
- recent commits
- latest checkpoints
- running jobs
- relevant artifacts/results
- context version/hash

Cache metadata:

- file path
- size
- mtime
- sha256
- summary version
- summary source hash
- last indexed/read ranges

Rules:

- unchanged content is referenced by hash instead of resent;
- large files are fetched by range;
- summaries are invalidated whenever source hash changes;
- resume package is deterministic and bounded by a configurable context budget.

Initial target budget:

- project brief: <= 2k tokens
- current task/checkpoint: <= 2k
- relevant file slices: <= 5k
- recent journal: <= 2k
- total default: about 11k tokens

## 9. MCP surface

Compatibility tools retained initially:

- list_dir
- read_file
- search
- run_command

New safe surface:

Agent:
- agent_register
- agent_heartbeat

Project:
- project_open
- project_status

Task:
- task_create
- task_claim
- task_status
- task_checkpoint
- task_resume
- task_release

Workspace:
- workspace_list
- workspace_read
- workspace_search
- file_edit_cas

Jobs:
- job_submit
- job_get
- job_wait
- job_logs
- job_result
- job_cancel

Git:
- git_status
- git_diff
- git_commit

Journal:
- journal_tail

The broad run_command tool becomes compatibility-only and must not be the foundation for durable execution.

## 10. Storage

Start with Python + SQLite WAL.

No Redis/Kafka/Postgres in v2 unless measured contention requires it.

Recommended DB modules:

- schema/migrations
- transaction helper
- operation repository
- job repository
- task repository
- lease repository
- event repository

SQLite requirements:

- WAL journal mode
- foreign_keys=ON
- busy_timeout
- short transactions
- unique constraints on operation_id
- indexed task/job/event lookup paths

## 11. Security

Keep:

- ROOT containment;
- symlink/path escape protection;
- OAuth 2.1 / PKCE / token rotation;
- redirect host allowlist;
- command allowlist.

Add:

- structured command specs instead of arbitrary shell strings for durable jobs;
- environment allowlist per job;
- redaction of known secrets from journal/log metadata;
- per-project allowed roots;
- maximum log/artifact quotas;
- process ownership validation before cancellation;
- explicit destructive-operation annotations.

No durable job may inherit the full server environment by default.

## 12. Milestones

### V2-0 — Baseline freeze and regression harness

Deliver:

- current source imported unchanged functionally;
- README and install/run instructions;
- baseline OAuth/tool tests;
- test workspace fixture;
- baseline security regression tests.

Exit gate:

- existing OAuth flow passes;
- existing file containment passes;
- existing MCP tools behave as before.

### V2-A — Durability core — PASS (2026-09-30)

Closure follows the prospectively frozen execution lock. Durable job/operation semantics are implemented and qualified. The earlier high-level `file_edit_cas` idea was not silently added because no exact CAS crash-consistency contract was frozen; it is explicitly carried into the V2-B workspace-isolation prelock.


A1. SQLite WAL + migrations.
A2. operation_id repository + idempotency.
A3. CAS file mutation.
A4. append-only event journal.
A5. durable Job model and supervisor.
A6. job_submit/get/logs/result/cancel.
A7. job_wait terminal/heartbeat modes.
A8. startup process reconciliation.
A9. retry policy + circuit breaker.
A10. compatibility bridge for current run_command.

Exit gate:

- a >10 minute child process survives the originating tool call;
- reconnecting client can recover by job_id;
- repeated job_submit with same operation_id creates exactly one process;
- terminal event is returned once per subscriber cursor;
- RemoteMCP restart does not duplicate a running job.

### V2-B — Multi-agent

B1. agent/session identity.
B2. task registry/state machine.
B3. heartbeat and expiring leases.
B4. task resume/recovery.
B5. project-level concurrency policy.
B6. Git worktree allocator.
B7. worktree/task branch cleanup and recovery.
B8. conflict/CAS tests with two or more simulated agents.

Exit gate:

- >= 3 agents operate concurrently on >= 2 projects;
- two agents cannot mutate the same protected working tree;
- dead agent lease recovers without killing its durable process;
- resumed agent receives deterministic task state.

### V2-BD — Multi-device execution routing

BD1. stable device_id per RemoteMCP installation.
BD2. device pairing/registration under one owner account.
BD3. project -> device binding.
BD4. task inherits project device.
BD5. single-endpoint routing to the correct execution device.
BD6. device heartbeat/offline semantics.
BD7. no silent cross-device task migration.
BD8. node authentication/transport replay protection.
BD9. two-device qualification.

Exit gate:

- one owner account can use at least two execution machines through one control-plane namespace;
- projects/tasks route deterministically to the bound device;
- device offline does not reassign or corrupt task/job/worktree state;
- reconnect restores routing without duplicating work;
- both login devices and execution devices coexist without last-login-wins behavior.

### V2-C — Context Broker

C1. project registry/manifest.
C2. file fingerprint index.
C3. bounded file range fetch.
C4. summary cache keyed by source hash.
C5. checkpoint resume package.
C6. recent journal compaction.
C7. context budget enforcement.
C8. invalidation tests.

Exit gate:

- reconnect can resume a representative research task using only the compact package plus explicitly requested slices;
- unchanged large files are not retransmitted;
- stale summaries are never returned after source mutation.

### V2-D — Native MCP Tasks integration

D1. capability detection.
D2. map internal jobs to io.modelcontextprotocol/tasks.
D3. native get/cancel/result semantics.
D4. fallback to job_* for clients without extension support.
D5. conformance/interoperability tests.

Exit gate:

- same underlying durable job works through both native Tasks and fallback job_* surfaces;
- disconnect/reconnect behavior is equivalent.

## 13. Test matrix

Durability:

- lost HTTP response after successful mutation;
- duplicate operation_id same payload;
- duplicate operation_id different payload;
- server restart while job RUNNING;
- worker exits while server is down;
- job cancellation races with natural completion.

Concurrency:

- 3/10/25 simulated agents;
- same-project read concurrency;
- same-file mutation conflict;
- expired lease takeover;
- concurrent worktrees.

Context:

- unchanged files;
- modified file invalidates summary;
- large lineage file partial reads;
- checkpoint + journal replay;
- bounded resume package.

Failure injection:

- 429;
- 502/503/504;
- DB busy;
- process spawn failure;
- disk full/quota;
- tunnel disconnect;
- OAuth refresh/reconnect.

## 14. Implementation order

Do not start with multi-agent concurrency.

Required order:

    V2-0 baseline freeze
      -> V2-A durability
      -> V2-B multi-agent + multi-login-device
      -> V2-BD multi-device execution routing
      -> V2-C context broker
      -> V2-D native MCP Tasks integration

B and C may overlap only after A's operation/job semantics are frozen.

## 15. First implementation slice

The first code change after this plan should be:

REMOTE_MCP_V2A_DURABLE_JOB_AND_OPERATION_PRELOCK

Freeze before implementation:

- SQLite schema v1;
- operation idempotency state machine;
- job state machine;
- job process ownership/fingerprint contract;
- log/result paths;
- terminal-event cursor/ack semantics;
- retryable vs non-retryable error classes;
- startup reconciliation rules;
- exact compatibility behavior of existing run_command.

Only after that prelock passes review should implementation begin.

## 16. Project operating protocol

These rules are mandatory for all subsequent RemoteMCP work.

### 16.1 End-of-turn next step

Every assistant turn that advances this project MUST end with an explicit:

`Bước tiếp theo:`

It must name exactly the next valid implementation/research step, or state that the current phase is blocked and what prerequisite must be satisfied before proceeding.

Do not leave the user to infer the next step from the body of the report.

### 16.2 README update gate after major phases

After every major phase completes, update `README.md` before opening the next phase.

A major phase includes at minimum:

- V2-0
- V2-A
- V2-B
- V2-BD
- V2-C
- V2-D

The README update must record:

- phase status: PASS / FAIL / BLOCKED;
- what capability was added or validated;
- current architecture/runtime surface;
- important operational changes;
- known limitations;
- exact next phase or next authorized step.

The next major phase MUST NOT be considered opened until this README update is committed/synchronized.

### 16.3 Phase closure order

Required closure sequence:

```text
phase implementation
    -> tests / QA
    -> phase verdict
    -> README update
    -> commit / sync
    -> only then open next phase
```