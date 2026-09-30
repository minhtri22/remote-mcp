# remote-mcp

RemoteMCP is a local MCP server for securely exposing a bounded workspace to authorized AI clients.

The current baseline provides OAuth-protected filesystem/search tools and allowlisted command execution. The v2 program evolves this into a durable multi-agent runtime with idempotent retry, persistent long-running jobs, task/lease coordination, Git worktree isolation, an append-only journal, and compact resume context.

## Current baseline

- Python
- MCP over Streamable HTTP
- OAuth 2.1-style authorization flow with PKCE
- workspace root containment
- redirect-host allowlist
- allowlisted command execution

Current tools:

Compatibility tools:

- `list_dir`
- `read_file`
- `write_file`
- `edit_file`
- `search`
- `run_command`

V2-A durable tools:

- `job_submit`
- `job_get`
- `job_wait`
- `job_logs`
- `job_result`
- `job_cancel`

V2-B multi-agent tools:

- `agent_register`, `agent_heartbeat`, `session_close`
- `project_register`, `project_status`
- `task_create`, `task_claim`, `task_status`, `task_checkpoint`, `task_block`, `task_set_ready`, `task_release`, `task_complete`
- `path_lease_acquire`, `path_lease_release`
- `file_write_cas`, `file_edit_cas`
- `task_job_submit`, `task_jobs`, `task_job_cancel`

## v2 direction

See [docs/REMOTE_MCP_V2_PLAN.md](docs/REMOTE_MCP_V2_PLAN.md).

Milestones:

1. **V2-0 Baseline freeze** — regression/security harness.
2. **V2-A Durability** — idempotent operations, SQLite WAL journal, durable jobs, retry/recovery.
3. **V2-B Multi-agent + multi-login-device** — identities, sessions, tasks, leases, CAS and Git worktree isolation.
4. **V2-BD Multi-device execution routing** — route projects/tasks to the correct RemoteMCP execution machine under one owner account.
5. **V2-C Context Broker** — manifests, hashes, cached summaries, bounded resume packages.
6. **V2-D MCP Tasks integration** — native `io.modelcontextprotocol/tasks` when supported, with `job_*` fallback.

## Long-running jobs

The v2 design does not keep one HTTP tool call alive for a multi-minute or multi-hour process.

Instead:

```text
job_submit
  -> durable job_id
  -> supervised process

job_get / job_wait / job_logs
  -> status/progress

job_result
  -> terminal result
```

This allows llama/Ollama/evaluation jobs to continue through browser disconnects or OAuth reconnects.

## Security

Do not commit passwords, OAuth state files, tokens, job environment secrets, or generated worktrees/logs.

This project is intended for a bounded local workspace exposed through an authenticated tunnel.

## Project operating protocol

For this repository:

- Every work/report turn ends with an explicit **Bước tiếp theo** naming the next valid step.
- After every major phase (V2-0, V2-A, V2-B, V2-BD, V2-C, V2-D), this README must be updated with the phase verdict, delivered capability, current limitations, and next authorized phase before work proceeds.

## Phase status

### V2-0 — Baseline freeze and regression harness: PASS

Closed: 2026-09-30.

Delivered:

- frozen six-tool MCP baseline and runtime constants;
- filesystem containment and file-operation regression coverage;
- OAuth/provider unit coverage;
- isolated full OAuth integration runner covering DCR, PKCE, OAuth resource binding, token exchange, MCP initialization, refresh rotation, revoke, traversal rejection, command allowlist rejection, and owner-password lockout;
- public tunnel smoke for OAuth metadata and unauthenticated Bearer challenge;
- explicit baseline documentation in `docs/V2_0_BASELINE_FREEZE.md`.

Final QA:

- `17 passed` regression/unit tests;
- full OAuth integration: PASS;
- public `https://remote.threadon.xyz` smoke: PASS;
- `git diff --check`: PASS.

Known baseline limitations:

- `run_command` replaces the child environment with only `PATH` + `HOME`. On Windows this can break Python `asyncio`/networking and Git network operations because required system variables such as `SystemRoot/WINDIR` are absent.
- `run_command` is request-bound, has a 60-second timeout, and kills the child on timeout; this is unsuitable for long-running llama/Ollama/research jobs.
- there is no durable operation identity, job recovery, multi-agent lease model, worktree isolation, journal, or context broker yet.

Next authorized phase:

`V2-A — Durability`, beginning with `REMOTE_MCP_V2A_DURABLE_JOB_AND_OPERATION_PRELOCK`.

### V2-A — Durability core: PASS

Closed: 2026-09-30.

Delivered:

- SQLite WAL schema v1 with migration checksum enforcement;
- durable `operation_id` idempotency and request-hash conflict detection;
- detached worker/supervisor model for long-running jobs;
- process ownership fingerprint using PID + creation time + executable + command hash;
- startup reconciliation with conservative `LOST/IN_DOUBT` handling and no automatic relaunch;
- append-only job events and per-subscriber terminal-event ACK cursors;
- durable stdout/stderr/result files;
- exact `job_submit`, `job_get`, `job_wait`, `job_logs`, `job_result`, `job_cancel` MCP tools;
- legacy `run_command` compatibility with the Windows child-environment defect repaired;
- default durable allowlist for the existing dev commands plus Ollama, the observed llama.cpp executable family, CMake/CTest/Ninja, uv and FFmpeg.

Qualification evidence:

- V2-A implementation tests: **27 passed**;
- V2-0 frozen regression: **17 passed**;
- full OAuth integration: **PASS**;
- public tunnel metadata/401 smoke: **PASS**;
- duplicate-submit: **PASS**, one operation produced exactly one payload;
- restart recovery: **PASS**, same payload PID/start token survived service restart;
- project-tool execution: **PASS** for Ollama, llama-cli, CMake, CTest, Ninja, uv and FFmpeg;
- durable long-job gate: **PASS**, same job completed after **640,732 ms** with exit code 0 and terminal event persisted.

Implementation notes:

- Windows transient `WinError 5` on atomic metadata replacement is handled by unique temp files plus bounded replace retry.
- launch nonces use an `ln_` prefix so argparse cannot misinterpret a nonce beginning with `-`.
- a 2-second stale-identity grace prevents very short jobs from being falsely classified LOST while `terminal.json` is being atomically published.

Known limitations / next-phase boundaries:

- V2-A assumes one active RemoteMCP supervisor per runtime directory; active-active multi-server scheduling is not claimed.
- agent/session/task leases and Git worktree isolation are not implemented yet.
- Context Broker/resume packages are not implemented yet.
- native MCP Tasks integration is deferred to V2-D.
- legacy `write_file/edit_file` remain compatibility tools; durable multi-agent file mutation requires a prospectively locked CAS/workspace-isolation contract rather than silently changing them.
- this repository closure does **not** automatically replace the currently running production process on port 8099; deployment/connector refresh is a separate operational action.

Detailed evidence: `docs/V2_A_IMPLEMENTATION_AND_QUALIFICATION.md`.

Next authorized phase:

`V2-B — Multi-agent`, beginning with `REMOTE_MCP_V2B_MULTI_AGENT_TASK_LEASE_WORKTREE_PRELOCK`.

### V2-B — Multi-agent + multi-login-device: PASS

Closed: 2026-09-30.

Delivered:

- stable single-owner `owner_account_id` independent from OAuth `client_id`;
- concurrent sessions from multiple login machines with no last-login-wins behavior;
- agent/session/task registries and heartbeat/lease lifecycle;
- task lease epoch + HMAC-derived replayable token without clear-token persistence;
- project concurrency limits;
- Git task worktrees and deterministic recovery;
- non-Git hierarchical FILE/TREE write leases;
- crash-reconcilable CAS mutation journal;
- managed-mode guards that block legacy mutation/execution bypasses;
- task-bound durable jobs that survive agent/session lease loss;
- exact 20 V2-B MCP tools layered on the 12 V2-A/compatibility tools.

Qualification evidence:

- V2-B implementation tests: **24 passed**;
- V2-A regression: **27 passed**;
- V2-0 regression: **17 passed**;
- full OAuth integration: **PASS** with 32-tool surface;
- public tunnel metadata/401 smoke: **PASS**;
- 3 agents / 2 projects / 3 OAuth-client sessions under one owner: **PASS**;
- two concurrent tasks in one Git project with distinct worktrees: **PASS**;
- competing claim exactly-one-winner: **PASS**;
- two-writer CAS race exactly-one-commit: **PASS**;
- worktree crash recovery with no duplicate worktree: **PASS**;
- durable job survives takeover; stale token rejected; current claimant cancellation succeeds: **PASS**;
- production port 8099 remained on PID **28464** throughout final QA.
- qualification harness uses the frozen **120-second task lease TTL**; takeover/expiry cases use controlled expiry injection rather than an out-of-contract short TTL.

Security/recovery notes:

- `owner-account.json` loss with persisted owner state fails closed;
- legacy `run_command` and direct `job_submit` are disabled once managed mode is active;
- registered-project writes must use task lease + CAS;
- Git recovery never performs automatic merge/rebase/reset/prune;
- task lease expiry moves work to `RECOVERABLE` but does not kill durable jobs.

Known limitations / next-phase boundaries:

- V2-B supports multiple login devices controlling the same execution node.
- It does **not** yet route work to multiple RemoteMCP execution machines.
- Context Broker, native MCP Tasks and the read-only `/ops` observability view remain deferred.
- V2-B source qualification was completed before deployment; production was then cut over separately under an operational release gate.

Detailed evidence: `docs/V2_B_IMPLEMENTATION_AND_QUALIFICATION.md`.

### V2-B production release: PASS

Deployed: 2026-09-30.

Operational cutover:

- previous production: `D:\\2.RemoteMCP`, PID **28464**, port **8099**;
- release snapshot: `D:\\2.RemoteMCP-releases\\673d09f`;
- release source commit: `673d09fc74c8a248ca52cfab7bf47dd68226f9b8`;
- current production PID: **11372** on `127.0.0.1:8099`;
- persistent OAuth state: `D:\\2.RemoteMCP-state\\oauth-state.json`;
- persistent V2 runtime: `D:\\2.RemoteMCP-state\\runtime`;
- rollback source/state snapshot retained under `D:\\2.RemoteMCP-backups\\pre-v2b-28464`.

Release gates:

- isolated production-like boot on port 8101: **PASS**;
- OAuth metadata: **PASS**;
- unauthenticated `/mcp` Bearer challenge: **401 PASS**;
- runtime schema migrations: **[1, 2] PASS**;
- existing OAuth connector session survived the cutover: **PASS**;
- public `https://remote.threadon.xyz` smoke after cutover: **PASS**;
- production runtime currently has **0 registered V2-B projects**, so managed mode has not yet disabled compatibility execution tools.

The tunnel was not repointed; it continues forwarding to local port 8099. No V2-B project should be registered until the consuming client refreshes the 32-tool schema and is ready to use task-scoped execution, because registering the first managed project intentionally disables legacy `run_command` and direct `job_submit`.

Release evidence: `docs/V2_B_PRODUCTION_RELEASE_2026-09-30.md`.

Next authorized phase:

`V2-BD — Multi-device execution routing`, beginning with `REMOTE_MCP_V2BD_MULTI_DEVICE_ROUTING_PRELOCK`.

### V2-BD — Routing implementation + zero-science qualification: PASS

Closed subphase: 2026-10-01.

Delivered:

- migration 003 and node-local schema v1;
- stable execution-device identity dev_* with Ed25519 pairing/revoke;
- outbound-only node transport through the existing public origin;
- timestamp/nonce/route-generation replay protection;
- ONLINE/OFFLINE/REVOKED semantics;
- project -> device binding and immutable task -> device inheritance;
- same-device durable command routing with no silent migration/failover;
- node-local crash-recoverable CAS;
- routed durable-job proxy backed by the V2-A node-local durable runtime;
- exact 12-tool V2-BD public surface, bringing the source-qualified MCP surface to 44 tools;
- Windows headless child-process execution so RemoteMCP workers/jobs do not open console windows or steal focus;
- target-device identity observability: device status and device-bound project/task/read/CAS/job results expose routing.device_id, device name, hostname and device state.

Routing rule:

    device_list / device_status
            ↓
    project_register_on_device(device_id, ...)
            ↓
    project_device_bindings
            ↓
    task_create
            ↓
    immutable task_device_bindings
            ↓
    task_* routed operations

Legacy run_command/read_file/write_file remain gateway-local compatibility tools and intentionally do not accept a device_id. A task-scoped call also does not accept a device override; it derives execution placement from the frozen task binding.

Qualification evidence:

- V2-BD implementation tests: **30/30 PASS**;
- V2-B regression: **24/24 PASS**;
- V2-A regression: **28/28 PASS**;
- V2-0 regression: **17/17 PASS**;
- OAuth integration: **PASS with 44 tools**;
- pairing/auth/command replay qualification: **PASS**;
- node CAS restart recovery: **PASS**;
- routed-job restart recovery: **PASS**;
- two-node loopback routing: **PASS**, no_cross_route=true;
- real-Windows headless-process qualification: **PASS** (console_handle=0, zero visible child windows, foreground unchanged);
- public tunnel smoke and post-implementation static validator: **PASS**.

A physical-machine-2 check against the still-deployed V2-B production surface returned HOSTNAME=DESKTOP-4PSD0G2 through legacy run_command. That is retained as negative evidence that the production 32-tool gateway-local surface must not be confused with V2-BD execution routing.

Current boundary:

- **V2-BD as a whole is not yet PASS**;
- the public production runtime is still the earlier V2-B release;
- no real V2-BD execution node has yet been paired for the physical pilot;
- no research project has been bound to a V2-BD execution device.

Detailed evidence:

- docs/V2_BD_ROUTING_IMPLEMENTATION_AND_ZERO_SCIENCE_QUALIFICATION.md
- docs/V2_BD_TARGET_DEVICE_IDENTITY_OBSERVABILITY_AMENDMENT.md
- specs/v2bd_implementation_qualification_manifest.json

Next authorized gate:

REMOTE_MCP_V2BD_REAL_TWO_EXECUTION_MACHINE_ISOLATED_PILOT_EXECUTION

The pilot must use isolated disposable roots on both physical machines and prove distinct dev_* identities, distinct physical host identity/marker evidence, project/task binding, routed-job execution on the expected host, offline/reconnect behavior, and no cross-route execution before V2-BD can be closed.
