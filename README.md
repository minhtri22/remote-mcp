# RemoteMCP

> Self-hosted MCP gateway and routed execution runtime for securely operating bounded workspaces from authorized AI clients.

## About

RemoteMCP provides an OAuth-protected MCP control plane for filesystem access, allowlisted command execution, durable jobs, multi-agent task coordination, CAS-safe mutation, Git worktree isolation, and explicit multi-device routing.

The project is designed around three principles:

- **self-hosted by default** — deployment URL, workspace roots, OAuth state, runtime state, and device identities remain under the operator's control;
- **fail closed** — no silent task migration, replacement pairing, or bypass of task/lease/CAS rules;
- **public-source safe** — the repository contains placeholders only for deployment-specific values; real domains, machine names, IDs, local paths, and credentials stay in ignored local configuration.

Current ChatGPT integration supports an app-backed Web wrapper and a separate direct-MCP Desktop package. The public repository never embeds a private deployment endpoint or a real ChatGPT app/plugin identifier.

## Architecture

```text
                         ┌──────────────────────────────┐
                         │       Authorized client      │
                         │  ChatGPT Web / Desktop / MCP │
                         └──────────────┬───────────────┘
                                        │
                    ┌───────────────────┴───────────────────┐
                    │                                       │
          Web: app-backed wrapper                Desktop: direct MCP
             (.app.json)                         (.mcp.json / mcp.json)
                    │                                       │
                    └───────────────────┬───────────────────┘
                                        │
                              HTTPS + OAuth + MCP
                                        │
                         ┌──────────────▼──────────────┐
                         │       RemoteMCP Gateway     │
                         │ OAuth / registry / routing  │
                         │ projects / tasks / journal │
                         └───────┬───────────┬─────────┘
                                 │           │
                         gateway-local   routed control
                         compatibility       plane
                                 │           │
                                 │     outbound-only nodes
                                 │       ┌────┴────┐
                                 │       │         │
                                 ▼       ▼         ▼
                            local work  node A    node B
                                      project(s) project(s)
                                          │         │
                                          └── tasks ┘
                                              │
                                   read / CAS / durable jobs
```

Routing is explicit:

```text
device
  ↓
project binding
  ↓
task inherits immutable placement
  ↓
task-scoped read / CAS / durable execution
```

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

V2-BD routed multi-device tools:

- `device_pair_begin`, `device_list`, `device_status`, `device_revoke`
- `project_register_on_device`, `project_bind_device`
- `task_list_dir`, `task_read_file`, `task_search`
- `task_job_get`, `task_job_logs`, `task_job_result`

## Quick start and guides

- [User guide — one machine, multiple machines, multiple projects](docs/USER_GUIDE.md)
- [ChatGPT private-plugin package setup](docs/CHATGPT_PLUGIN_SETUP.md)
  - Web verification: `RemoteMCP V2 Web` v1.3.0 app-backed packaging passed a fresh ChatGPT Web `device_list` call on 2026-10-03.
  - Desktop: `desktop-direct-mcp` keeps direct MCP manifests and is expected to be Desktop only.
  - Web: `web-app-ref` contains `.app.json`, contains no direct MCP manifest, and requires an existing eligible ChatGPT App ID.
- [Gateway timeout recovery and watchdog](docs/GATEWAY_RECOVERY.md)

### ChatGPT operator commands

Command sources are first-class files under:

```text
commands/
├── _conventions.md
├── status.md
├── devices.md
└── restart.md
```

The plugin builder copies the user-facing command files into the generated package instead of embedding command Markdown inside Python.

Current staged commands:

- `/status` — compact gateway/device health summary;
- `/devices` — read-only inventory of every gateway-visible execution device;
- `/restart <device>` — safely restart one execution node, with active-job protection and same-identity verification.

There is no `/deviceList` alias. The canonical inventory command is `/devices`.

These command/backend additions remain source-staged until the corresponding release and plugin package are deployed/refreshed; modifying these source files alone does not interrupt currently connected agents.

### Start or restart an execution node on Windows

The repository root includes `Start-RemoteMCP-Node.ps1`. Users should use this script instead of reconstructing the Python/venv command manually.

Normal production node:

```powershell
.\Start-RemoteMCP-Node.ps1
```

Restart the same node identity:

```powershell
.\Start-RemoteMCP-Node.ps1 -Restart
```

Use an existing non-default runtime identity:

```powershell
.\Start-RemoteMCP-Node.ps1 -RuntimeDir "C:\path\to\existing\runtime"
```

The script reuses the existing device runtime/key, creates or reuses the dedicated RemoteMCP virtual environment, ensures the pinned node dependencies are available, avoids duplicate node processes, starts the node headlessly, and prints local node status. It does **not** pair a new device.

Current practical readiness:

- one-machine managed work: established;
- two real execution machines with explicit project/task routing: established;
- routed read, CAS mutation and durable jobs on the expected physical host: established;
- node reconnect after gateway restart: established;
- full 44-tool ChatGPT surface: established;
- physical offline/no-failover and remaining restart/replay recovery gates: still pending.

The routing capability is ready for **supervised project deployments**, but the two current physical pilot identities remain pilot-only and must not be rebound to real research projects before the pilot is formally closed. Unattended multi-device production readiness is not yet formally closed.

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

## Releases

The repository currently uses source commits and qualification records as the authoritative development history.

GitHub Release assets are intentionally not required for a self-hosted deployment. When formal releases are published, they must be reproducible from a tagged public commit and must contain **no deployment-specific configuration**.

Public release notes may include:

- source commit/tag;
- feature and compatibility summary;
- migration requirements;
- qualification/test verdicts;
- generic upgrade/rollback instructions using placeholders.

They must not include real domains, machine names, device/session IDs, local filesystem paths, OAuth/client identifiers, connected-account names, or credentials.

## Packages

RemoteMCP does not require a central hosted package or service.

ChatGPT plugin archives are built locally from public source:

```powershell
# Desktop direct-MCP package
$env:PUBLIC_URL = "https://mcp.example.com"
python scripts/build_chatgpt_plugin.py `
  --target desktop-direct-mcp

# Web app-backed wrapper
$env:REMOTEMCP_CHATGPT_APP_ID = "<CHATGPT_APP_ID>"
python scripts/build_chatgpt_plugin.py `
  --target web-app-ref `
  --plugin-name remote-mcp-v2-web `
  --display-name "RemoteMCP V2 Web"
```

Generated archives belong under local build output such as `dist/` and should not contain OAuth tokens, owner passwords, device keys, pairing secrets, real runtime paths, or private deployment URLs unless the operator is intentionally building a private local artifact.

GitHub Packages is optional and is not required by the runtime.

## Security

Do not commit passwords, OAuth state files, tokens, job environment secrets, generated worktrees/logs, private deployment hostnames, physical machine names, device/session identifiers, or machine-specific filesystem paths.

Private deployment metadata belongs in the local gitignored `.env` file. Copy `.env.example` and replace only placeholder values locally; never commit the populated file. Variables include `PUBLIC_URL`, `PORT`, `MCP_ROOT`, `MCP_STATE`, `MCP_RUNTIME_DIR`, redirect hosts, qualification paths, and optional ChatGPT app references. `OWNER_PASSWORD` must not be stored in `.env`; the Windows gateway configurator stores it separately with DPAPI.

Public docs/specs use placeholders such as `https://mcp.example.com`, `MACHINE_A_HOST`, and `<PRIVATE_STATE_ROOT>`.

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
- public `https://mcp.example.com` smoke: PASS;
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
- this repository closure does **not** automatically replace the currently running production gateway process; deployment/connector refresh is a separate operational action.

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
- production gateway remained on port `<GATEWAY_PORT>` with the expected process identity throughout final QA.
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

- previous production: `<PREVIOUS_RELEASE_ROOT>`, PID `<PREVIOUS_GATEWAY_PID>`, port `<GATEWAY_PORT>`;
- release snapshot: `<RELEASE_ROOT>\\<RELEASE_ID>`;
- release source commit: `673d09fc74c8a248ca52cfab7bf47dd68226f9b8`;
- current production PID: `<GATEWAY_PID>` on `127.0.0.1:<GATEWAY_PORT>`;
- persistent OAuth state: `<PRIVATE_STATE_FILE>`;
- persistent V2 runtime: `<PRIVATE_RUNTIME_DIR>`;
- rollback source/state snapshot retained under `<PRIVATE_BACKUP_ROOT>`.

Release gates:

- isolated production-like boot on `<QUALIFICATION_PORT>`: **PASS**;
- OAuth metadata: **PASS**;
- unauthenticated `/mcp` Bearer challenge: **401 PASS**;
- runtime schema migrations: **[1, 2] PASS**;
- existing OAuth connector session survived the cutover: **PASS**;
- public `https://mcp.example.com` smoke after cutover: **PASS**;
- production runtime currently has **0 registered V2-B projects**, so managed mode has not yet disabled compatibility execution tools.

The tunnel was not repointed; it continues forwarding to local `<GATEWAY_PORT>` configured outside the public repository. No V2-B project should be registered until the consuming client refreshes the 32-tool schema and is ready to use task-scoped execution, because registering the first managed project intentionally disables legacy `run_command` and direct `job_submit`.

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
- target-device identity observability: device status and device-bound project/task/read/CAS/job results expose routing.device_id, device name, hostname and device state;
- additive Windows join UX: `device_pair_begin` emits both an instance-bound `RemoteMCP-Join.ps1` and a one-line HTTPS `join_command`; both bootstrap dependencies/source, consume the single-use pairing bundle without manual `pairing_id`, start the outbound node headlessly and install per-user auto-start;
- node transport now survives transient gateway/network interruptions with bounded reconnect backoff instead of exiting; terminal revoke/signature/route-generation failures still fail closed;
- the self-hosted gateway serves `/device/v1/node-bundle.zip`, so normal node join/update no longer depends on GitHub availability.

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

- current V2-BD regression suite: **35/35 PASS** (the original routing implementation qualification closed at 30/30);
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

A physical-machine-2 check through legacy `run_command` returns the gateway host because legacy compatibility tools are intentionally gateway-local. It is not execution-routing evidence; routed evidence must use the V2-BD device/project/task surfaces.

Current boundary:

- **V2-BD as a whole is not yet PASS**;
- production currently runs release `721a691` with the full **44-tool** MCP surface;
- the real two-machine pilot has two distinct execution identities: `MACHINE_A_HOST` (`dev_A`) and `MACHINE_B_HOST` (`dev_B`);
- both disposable pilot projects were registered on the intended device and tasks inherited immutable device placement;
- routed marker reads returned `PHYSICAL_MACHINE_1_ONLY` and `PHYSICAL_MACHINE_2_ONLY` from the correct hosts;
- routed CAS probes remained isolated to their intended machine;
- routed durable jobs succeeded on both physical hosts and reported the expected `REMOTEMCP_DEVICE_ID`;
- the node reconnect fix survived a real gateway restart on machine 1 without replacing its device identity;
- the ChatGPT consumer surface has been verified to expose the V2-BD device tools;
- remaining physical qualification is the explicit offline/no-failover, same-identity reconnect, durable-job restart/replay, stale-generation/signature and CAS recovery chain;
- those disruptive failure/recovery gates are currently under an **operational hold** because an active SIX experiment requires uninterrupted two-machine connectivity; machine 2 was observed ONLINE with active routed work when the hold was entered;
- no real research project is part of the pilot evidence; only disposable pilot projects are bound.

Operational readiness:

- **supervised project deployments: capability available**;
- **current two-machine pilot identities/roots: pilot-only; do not bind real research projects yet**;
- **unattended multi-device production readiness: pending remaining physical failure/recovery gates**.

Detailed evidence:

- docs/V2_BD_ROUTING_IMPLEMENTATION_AND_ZERO_SCIENCE_QUALIFICATION.md
- docs/V2_BD_TARGET_DEVICE_IDENTITY_OBSERVABILITY_AMENDMENT.md
- docs/V2_BD_ONE_FILE_JOIN_UX.md
- docs/V2_BD_FAILURE_RECOVERY_OPERATIONAL_HOLD.md
- docs/USER_GUIDE.md
- docs/CHATGPT_PLUGIN_SETUP.md
- specs/v2bd_implementation_qualification_manifest.json

### Staged operator/recovery UX (source-next, not yet deployed)

The current `main` branch additionally stages:

- root `Start-RemoteMCP-Node.ps1` runtime discovery that reuses an existing paired identity and refuses to invent a new runtime identity;
- routed `device_restart` with active-job protection;
- ChatGPT plugin command files for `/status` and `/restart <device>`;
- out-of-band gateway recovery scripts and a local watchdog for full MCP endpoint timeouts.

These changes are **source-staged only** while the active SIX two-machine experiment holds disruptive production deployment. Current production remains on the previously deployed 44-tool surface until a safe deployment window is explicitly opened.

Current operational gate:

`V2BD_FAILURE_RECOVERY_GATES_OPERATIONAL_HOLD_DUE_TO_ACTIVE_SIX_TWO_MACHINE_EXPERIMENT`

While this hold is active, both primary nodes must remain ONLINE. Do not stop/restart/revoke machine 2 or open any qualification gate that intentionally interrupts its RemoteMCP connectivity. This hold is neither PASS nor FAIL for the deferred failure/recovery gates.

Release condition: SIX explicitly no longer requires uninterrupted two-machine connectivity, or an explicit maintenance window is granted.

After hold release, the next qualification gate is:

`NODE_2_OFFLINE_NO_FAILOVER_AND_SAME_IDENTITY_RECONNECT_GATE`


### Public repository sanitization + command layout static QA: COMPLETE

Closed: 2026-10-02.

Delivered:

- public current-main deployment metadata sanitization;
- gitignored local `.env` configuration with generic `.env.example`;
- DPAPI-only owner-password persistence outside `.env`;
- first-class `commands/status.md`, `commands/devices.md`, and `commands/restart.md`;
- canonical `/devices` command with no `/deviceList` alias;
- plugin builder packaging command source files directly;
- public-repo hygiene regression checks;
- README/User Guide/Plugin Setup documentation aligned with the command surface.

This closure is source/static only. It did not deploy or restart the running RemoteMCP gateway/nodes and did not refresh the installed plugin, so active agents were not interrupted.

Detailed record: [docs/PUBLIC_REPO_CURRENT_MAIN_SANITIZATION_AND_COMMAND_LAYOUT_STATIC_QA.md](docs/PUBLIC_REPO_CURRENT_MAIN_SANITIZATION_AND_COMMAND_LAYOUT_STATIC_QA.md).

History rewrite remains deferred until no active agent depends on the current commit graph.

Next history gate when a maintenance window is explicitly available:

`PUBLIC_REPO_GIT_HISTORY_PRIVATE_METADATA_PURGE_AND_FRESH_CLONE_VERIFICATION`
