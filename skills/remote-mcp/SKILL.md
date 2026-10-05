---
name: remote-mcp
description: Use the full RemoteMCP V2 surface, including multi-device execution routing, with mandatory execution-identity and pairing governance preflight.
---

# RemoteMCP V2

## Mandatory managed-execution preflight

Run this preflight before any action that could pair, re-pair, revoke, replace, restart, register, bind, or relaunch execution.

1. Start read-only. Use `device_list`, then `device_status` for the intended existing device when one is known.
2. Reuse an existing project, task, and device placement when prior context already identifies them.
3. Classify the failure domain before mutation:
   - ChatGPT plugin/session binding;
   - connector/account-link authorization;
   - gateway/control-plane availability;
   - execution-node process/heartbeat;
   - managed project exposure/root mismatch;
   - scientific job observability.
4. Preserve an existing approved execution identity. A pairing ticket is capability material, not authorization to create or replace a node.
5. If a project is outside an existing node root, stop with `BLOCKED_ON_MANAGED_PROJECT_EXPOSURE_OR_EXPLICIT_DEDICATED_NODE_GATE`. Do not repair that condition by widening the root, auto-creating another device identity, re-pairing the existing device, or moving the project implicitly.
6. A dedicated node may be created only when the current user instruction or an already-approved gate explicitly authorizes it and identifies the exact device role/name, exact project root, and exact runtime directory. Do not broaden an approved project root to its parent.
7. Pairing secrets must never be transported through `run_command`, `task_job_submit`, job argv, shell arguments, task payloads, repository files, logs, checkpoints, or scientific lineage. Use only a dedicated local/managed pairing mechanism intended to consume the secret without persisting it.
8. Do not use a research/scientific task as a transport for infrastructure repair.
9. Loss of job observability is not authorization to resubmit or relaunch a scientific job. Read existing task/job state first. Relaunch only with explicit authorization.
10. Connector/OAuth/gateway errors are not evidence that an execution node must be restarted. Restart a node only when the node/process failure is independently established and the requested restart is authorized.
11. Before any restart with active routed jobs, require explicit confirmation. After restart, verify the same device ID, key fingerprint, and route generation expected by the operation.
12. If ChatGPT reports the canonical Web plugin as unavailable/not installed, or a tool reports that `link_id` is not an eligible linked account, classify the condition as `PLUGIN_SESSION_BINDING_FAILURE`. Do not pair/re-pair, create a node, widen a root, register a replacement project, restart `machine-1`, or relaunch a scientific job as remediation.
13. For ChatGPT Web, the canonical private plugin identity is `remote-mcp-v2-web-clean`. The older `remote-mcp-v2-web` identity is legacy direct-MCP and must not be selected as the canonical Web route.
14. If identity, approved root, approved runtime, plugin/session binding, or authorization is ambiguous, fail closed and report the blocked prerequisite instead of inventing a route.

The machine-readable companion policy is `skills/remote-mcp/managed_execution_policy.json`. Its decisions are governance constraints, not suggestions.

## ChatGPT Web canonical identity and session binding

For ChatGPT Web:

- canonical plugin identity: `remote-mcp-v2-web-clean`;
- legacy identity: `remote-mcp-v2-web`;
- canonical display name must be visibly distinct from the legacy display name;
- the canonical package must be app-backed and must not contain `.mcp.json` or `mcp.json`;
- a plugin/session/account-link failure is a control-plane attachment failure, not evidence that the gateway or execution node failed;
- start diagnosis read-only: confirm canonical plugin metadata, app-backed dependency, and an eligible linked account before any execution mutation;
- an existing conversation can retain stale plugin/tool attachment state after a private plugin release changes; use a fresh conversation for the final smoke test when session freshness matters.

If the canonical plugin cannot be resolved in the current conversation, stop with `PLUGIN_SESSION_BINDING_FAILURE` and repair the ChatGPT plugin/session attachment. Do not fall back to the legacy Web identity or to node pairing.

## Routing and project placement

Use `device_list` and `device_status` to inspect execution nodes.
If no execution devices are paired, local project tools may be used on the gateway.
For multi-device work, use `project_register_on_device` or `project_bind_device` to choose placement only after the mandatory preflight above.
Before registering a project again, reuse an existing project/task when the user or prior context already identifies it.
Tasks inherit immutable project-to-device placement; do not try to override a task's device.
Use `task_read_file`, CAS mutation tools, and `task_job_*` for routed task work.
Legacy `run_command`, `read_file`, and `write_file` are gateway-local compatibility tools and are not evidence of execution routing.
When managed projects exist, prefer task-scoped tools over legacy execution/mutation tools.

### Project workspace isolation

Every managed project owns one deterministic MCP workspace:

`.remotemcp/workspaces/{project_id}`

Git task worktrees for that project must live only under:

`.remotemcp/workspaces/{project_id}/worktrees/{task_id}`

Rules:

- agents never choose or invent a worktree filesystem path;
- task creation computes the worktree path from `project_id + task_id`;
- the execution node independently validates the same path before provisioning or executing;
- direct managed-job mutations through `git worktree add/move/remove/prune/repair/lock/unlock` are forbidden; use RemoteMCP task lifecycle APIs instead;
- read-only `git worktree list` is allowed for diagnostics;
- existing legacy `.remote-worktrees/{project_id}/{task_id}` tasks remain valid in place until safe closure or an explicitly authorized migration;
- never move, delete, or prune an active/dirty legacy worktree merely to make the filesystem tidy;
- arbitrary sibling directories such as `CLDP-SIX-*`, `CQG-*`, or other agent-selected worktree names outside the project workspace are not valid managed worktrees.

Use the `workspace_rel` and `worktrees_root_rel` returned by project status as the authoritative placement for future work.

## Pairing and dedicated-node rules

Before `device_pair_begin`, prove all of the following:

- there is no already-approved identity for the intended execution role that should be preserved;
- an explicit dedicated-node gate exists;
- exact device role/name, project root, and runtime directory are already approved;
- the requested root exactly matches the approved root;
- a secure local/managed pairing path exists.

If any item is missing, do not create a ticket merely to "try pairing." Report the prerequisite that is missing.

Never use pairing to repair a project-root mismatch, connector/session failure, gateway failure, or temporary node observability problem.

## Scientific continuity

When a scientific task/job may already exist:

- inspect `task_status`, `task_jobs`, `task_job_get`, `task_job_logs`, or `task_job_result` as appropriate;
- preserve the existing task/job identity;
- do not submit a replacement run because status retrieval failed;
- infrastructure diagnostics and maintenance records do not belong in scientific `LINEAGE.md` unless the scientific protocol explicitly says otherwise.

### Post-run scientific evidence readback

For routed scientific jobs that write canonical evidence outside the managed task worktree, use `task_job_submit_with_evidence` and declare every exact evidence file **before execution** with `evidence_paths`. The locked `task_job_submit` tool remains unchanged for compatibility. Do not use a broad directory or glob; declare exact files.

After the routed job is terminal:

1. Do not rerun science because `task_read_file` failed. `task_read_file` is task-root scoped and can legitimately return `PATH_ESCAPE` or `NOT_FOUND` for an external evidence file even when the scientific job succeeded.
2. Call `task_job_artifact_status(task_id, proxy_job_id, path)`. This reconciles the exact existing job, requires authoritative terminal evidence, and checks the predeclared artifact without creating another scientific job.
3. If status returns `readback_state=READY`, pin the returned SHA-256 and call `task_job_artifact_read(..., expected_sha256=...)`. Repeating this read is safe because it is read-only and never creates or relaunches a scientific job.
4. If status reports `POSTRUN_EVIDENCE_PATH_UNDECLARED`, the job is legacy or the path was not preregistered. Treat this as an evidence-readback limitation, not a scientific failure. Do not widen filesystem access, use SQL/node-db edits, or rerun the scientific one-shot.
5. If status reports `POSTRUN_EVIDENCE_NOT_FOUND`, preserve the terminal job result and classify the artifact as missing post-run evidence. Do not manufacture or regenerate it unless a separate scientific recovery gate explicitly authorizes that.
6. `JOB_RESULT` and stdout/stderr may establish execution integrity, but they do not silently replace a canonical result file when the scientific protocol requires that file.

### Routed execution lifecycle recovery

Classify the failure phase before taking any recovery action. Use the same logical submit/job identity whenever one already exists.

- **Before proxy creation / admission-dispatch failure:** call `task_job_submit_failure_status(task_id, operation_id)`. New routed submits freeze their exact argv/cwd/evidence intent before predecessor reconciliation, so a later agent can recover without reconstructing scientific parameters from chat history.
- If status returns `preproxy_recoverable=true`, reclaim the task lease if needed, pin the returned `argv_sha256`, and call `task_job_recover_preproxy_submit`. The tool authoritatively reconciles earlier routed predecessors first, then creates the first proxy for the **same original operation_id** and reuses the frozen argv/cwd/evidence declaration. It must not create a replacement logical one-shot.
- Older operations created before submit-intent freezing may return `ADOPT_LEGACY_SUBMIT_INTENT`. Use `task_job_adopt_legacy_submit_intent` only when the exact original argv/cwd are available. The tool accepts them only if they reproduce the historical operation request hash; otherwise it fails closed.
- If the original failure is nonrecoverable, task/device identity changed, an earlier job is still genuinely active, or predecessor state is not authoritative, do not bypass the gate with SQL, a new operation, another proxy, or a second scientific run.
- Once a proxy exists, switch to the proxy-scoped workflow below. Once a job is terminal, switch to post-run evidence readback. Do not keep using pre-proxy recovery after execution has started.

Decision order:

`task_job_submit_failure_status`
→ `task_job_recover_preproxy_submit` when eligible
→ `task_job_recovery_status` / `task_job_recover_path_escape` when a proxy exists but no node job exists
→ `task_job_get` / `task_job_result` while executing or terminalizing
→ `task_job_artifact_status` / `task_job_artifact_read` for declared post-run evidence.

### Pre-execution routed-submit recovery

If a routed scientific job is still `QUEUED` with `node_job_id=null`, do not create a second proxy/job as the first response.

1. Call `task_job_recovery_status(task_id, proxy_job_id)`.
2. Recovery is automatically eligible only for the narrow case where the original `JOB_SUBMIT` failed with `PATH_ESCAPE` before node-job creation, the routed row is still pre-execution `QUEUED`, there is no terminal evidence, and the original task/proxy identity is intact.
3. The status response returns the frozen original argv SHA-256. Inspect the executor/entrypoint first and independently establish that changing cwd to the managed task root `.` does not change scientific semantics.
4. Only after that check, call `task_job_recover_path_escape` with the returned argv hash and `acknowledge_cwd_semantics_preserved=true`.
5. The recovery tool must reuse the same `proxy_job_id` and exact original argv. It first probes the bound node for the exact proxy; if a mapping already exists it repairs observability instead of submitting. If authoritative absence is proven, it issues one idempotent same-proxy recovery submit with cwd `.`.
6. If status is not eligible, the argv hash does not match, the node probe is ambiguous, the device is offline, or cwd semantics cannot be proven equivalent, fail closed. Do not repair with SQL, node-database edits, raw node internals, a replacement proxy, or a second scientific one-shot.
7. After recovery returns a `node_job_id`, track only that same proxy through `task_job_get`, `task_job_logs`, and `task_job_result`. Do not launch another recovery or replacement run.

## Operator commands

- `/status`: show a compact gateway/device health summary.
- `/devices`: list all gateway-visible execution devices using `device_list`.
- `/restart <device>`: restart one execution node using `device_restart`.
  Run the mandatory preflight first.
  If more than one device exists and no target is supplied, ask the user to choose; never guess.
  If the target reports active routed jobs, do not set `allow_active_jobs=true` without explicit user confirmation.
  After restart, verify the same device_id, fingerprint, and route_generation.
- `/restart gateway`: explain that an unresponsive gateway cannot restart itself through the same MCP endpoint; use the host/out-of-band restart path instead.

There is no `/deviceList` alias. Use `/devices`.

If a client does not expose a native slash-command picker but sends these strings as ordinary chat text, follow the same semantics.


### Windows research-storage policy

On the production Windows research host, the Windows OS drive is **forbidden** for research repositories, managed project roots, managed workspaces, and task worktrees.

- canonical production research root: `D:\WORK\RESEARCH`;
- paths under `%USERPROFILE%\RemoteMCP-Workspace` are historical-only and must not be reused as production research roots;
- `%LOCALAPPDATA%\RemoteMCP` may contain only RemoteMCP runtime identity, durable state, logs, and virtual environments;
- never create, clone, expose, migrate, or materialize a research repository/worktree on the OS drive as a workaround for `PATH_ESCAPE` or project-root mismatch;
- do not repair root mismatch by re-pairing, replacing the device identity, or editing `device.json` / node DB by hand;
- use an explicit same-identity node root override on the approved non-OS research root, then register the canonical project under that root;
- any historical C-drive project binding must be labeled `HISTORICAL_ONLY_DO_NOT_REUSE` and must not be selected for new scientific work.

If a command, join flow, starter, watchdog, handoff, or agent proposes `C:\...` for a research/project/worktree path, treat it as a policy violation and fail closed. Runtime-only paths under LocalAppData are not research storage and are exempt.


### Secure local dedicated-node pairing

When a dedicated node must be paired on the same host as the gateway, use `device_pair_local_dedicated_node` after `device_pair_begin`.

- pass only `pairing_id`, exact device name, exact node root, exact runtime directory, and explicit scope acknowledgement;
- never pass the pairing code through `run_command`, routed jobs, argv, repo files, logs, checkpoints, or lineage;
- the tool derives and consumes the one-time credential inside the gateway process;
- it refuses to replace an existing paired identity;
- if a crash occurs after central consume, replay is allowed only when the exact local key fingerprint matches the central paired device;
- pairing does not start the node process; start/supervision remains a separate lifecycle action.



### Authoritative resource capacity

Do not use `device_list.active_routed_jobs` or the central routed-job registry count as a resource-contention gate. Those values are inventory/observability state and may contain stale or historical nonterminal rows.

Before a fresh one-shot, resource-sensitive execution, or node restart:

1. call `device_capacity_status(device_id)`;
2. require `capacity_resolved=true`;
3. require the node heartbeat to report `capacity_reconciliation_complete=true` and zero unresolved node rows;
4. use only `authoritative_active_node_jobs`, which is computed after the node reconciles every nonterminal routed row against durable job state;
5. if the authoritative count is positive, recheck CPU/RAM and identify whether the live workload materially contends with the planned run;
6. if the authoritative count is zero, continue the remaining CPU/RAM/disk/model-cache resource gates;
7. if the heartbeat is from an older node, unreconciled, stale, unavailable, contains unresolved rows, or the device is offline, treat capacity as unresolved and fail closed.

A large `registry_nonterminal_routed_jobs` value by itself must never block a scientific transition. It is an inventory/audit signal, not a capacity signal. Likewise, an unreconciled node-routed count must never be presented as authoritative capacity.


### Managed task base pinning and claim recovery

For a device-bound Git project, `task_create` must not return a READY task with a null `base_commit`. Resolve the requested `base_ref` on the bound device first. A branch that exists on `origin` but is not yet present in the local canonical repository may be fetched as an exact remote-tracking ref and then pinned to its immutable commit. Worktree materialization must use that pinned commit and must not silently re-resolve a moving branch.

Legacy routed tasks with `base_commit=null` are infrastructure state, not scientific failures. On the next managed claim, resolve and pin the exact base on the bound device before creating the worktree. Do not bypass a failed/pending managed claim by switching to deterministic local execution.

`DEVICE_COMMAND_PENDING` and `DEVICE_COMMAND_EXPIRED` during project/task machinery are recoverable control-plane conditions. Retry the same operation identity. An expired routed command is requeued using the same command id/request hash; do not create a replacement scientific execution or change the scientific contract. Control-plane materialization commands have dispatch priority over observation backlog.
