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
