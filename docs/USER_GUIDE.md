# RemoteMCP User Guide

This guide covers the practical topologies that RemoteMCP supports today: one computer, multiple execution computers, and multiple projects.

## 1. Mental model

RemoteMCP has four distinct concepts:

- **Gateway** — the MCP server that ChatGPT connects to. It owns OAuth state, durable control-plane state, device registry, project/task bindings, and routing decisions.
- **Execution device** — a computer running the outbound RemoteMCP node. Each paired node has a stable `dev_*` identity and an Ed25519 key.
- **Project** — a registered working directory. In multi-device mode a project is bound to one execution device.
- **Task** — a unit of work inside a project. A task inherits the project's device binding and cannot silently move to another device.

The gateway and an execution device may be the same physical computer, but they are different roles.

## 2. One-computer setup

Use this when ChatGPT and all project work should run on the gateway computer.

1. Run the RemoteMCP gateway with a public HTTPS `PUBLIC_URL`.
2. Install the generated ChatGPT plugin package for that gateway.
3. Authorize the plugin through the gateway OAuth flow.
4. If no execution nodes are paired, register a local project with `project_register`.
5. Create and claim tasks, then use task-scoped CAS/job tools for managed work.

For simple compatibility work, the legacy tools (`read_file`, `write_file`, `run_command`) operate on the gateway workspace. Once managed projects exist, RemoteMCP intentionally blocks legacy mutation/execution paths that would bypass task/lease/CAS rules.

### When to pair the same machine as a node

You do not need to pair the gateway machine as a node for a one-computer setup. Pair it only if you want to use the same explicit `dev_*` routing model that you will later use across several machines.

### Start the node again after a reboot or manual stop

From the RemoteMCP repository root on Windows:

```powershell
.\Start-RemoteMCP-Node.ps1
```

The script:

- uses `%LOCALAPPDATA%\RemoteMCP\runtime` by default;
- creates/reuses `%LOCALAPPDATA%\RemoteMCP\node-venv`;
- ensures `httpx==0.28.1` and `cryptography==46.0.6` are installed;
- detects an already-running node and avoids starting a duplicate;
- starts the outbound node headlessly;
- prints the persisted local device status;
- never performs a new pairing.

For an existing identity stored elsewhere:

```powershell
.\Start-RemoteMCP-Node.ps1 -RuntimeDir "C:\path\to\runtime"
```

To deliberately restart only that RemoteMCP node process:

```powershell
.\Start-RemoteMCP-Node.ps1 -Restart
```

Do not use `-Restart` while a workload requires uninterrupted node connectivity.

## 3. Multiple execution computers

Recommended topology:

```text
ChatGPT
   |
   v
RemoteMCP gateway
   |
   +--> dev_A  (computer A)
   |
   +--> dev_B  (computer B)
```

Execution nodes are outbound-only. They do not need an inbound port or their own public tunnel.

### Add another computer

From ChatGPT, call:

```text
device_pair_begin(operation_id, device_name)
```

The response includes a short-lived, single-use `join_command`. Run that one PowerShell command on the new Windows machine. The join flow:

- obtains the node runtime from the self-hosted gateway;
- creates the local Ed25519 device identity;
- pairs once;
- stores the stable `dev_*` identity locally;
- starts the node headlessly;
- installs per-user auto-start.

After joining, verify with `device_list` or `device_status`.

### Register a project on a specific machine

Use:

```text
project_register_on_device(operation_id, device_id, path)
```

`path` is relative to that node's configured root.

Once the project is registered, new tasks inherit the device binding:

```text
project -> device
task -> inherited immutable device
```

Do not use legacy `run_command` as proof of multi-device routing. Legacy compatibility tools are gateway-local by design.

## 4. Multiple projects

One execution device can host many projects, and different projects can be placed on different devices.

Example:

```text
dev_A
  |- project-alpha
  |- project-beta

dev_B
  |- project-gamma
  |- project-delta
```

Register each project explicitly on the intended device.

For a routed task, use task-scoped tools:

- `task_list_dir`
- `task_read_file`
- `task_search`
- `file_write_cas`
- `file_edit_cas`
- `task_job_submit`
- `task_job_get`
- `task_job_logs`
- `task_job_result`

A task does not accept a device override. Placement comes from the frozen task binding.

## 5. Git projects and task isolation

For Git projects, task claims create task-specific worktrees. This prevents two agents/tasks from editing the same checkout by accident.

The intended flow is:

```text
project_register[_on_device]
        |
        v
task_create
        |
        v
task_claim
        |
        +--> isolated worktree
        |
        +--> CAS mutations
        |
        +--> durable task jobs
```

RemoteMCP does not automatically merge, rebase, reset, or prune user Git history as part of crash recovery.

## 6. What happens when a device goes offline?

RemoteMCP tracks device heartbeat state as `ONLINE`, `OFFLINE`, or `REVOKED`.

The routing contract is **no silent failover**: a task bound to one device must not be reassigned to another execution device just because its device is offline.

The physical two-machine pilot has already established correct positive routing on both machines, including routed reads, CAS mutations, and durable jobs returning the expected physical hostname and `REMOTEMCP_DEVICE_ID`.

The remaining physical pilot still needs to close the explicit offline/no-failover and recovery gates before unattended multi-device workloads are declared production-ready.

## 7. Current readiness

As of the current V2-BD pilot:

| Scenario | Status |
| --- | --- |
| One gateway / one local project | Established |
| Two real execution computers registered simultaneously | Established |
| Project -> device binding | Established |
| Immutable task -> device inheritance | Established |
| Routed read/search | Established |
| Routed CAS mutation | Established |
| Routed durable job on the expected physical host | Established |
| Node reconnect after gateway restart | Established |
| ChatGPT 44-tool surface | Established |
| Physical offline/no-failover gate | Pending |
| Full physical restart/replay/CAS recovery chain | Pending |
| Unattended production-readiness verdict | Pending |

This means the RemoteMCP routing capability can already support **supervised project deployments**. However, the two execution identities used by the current physical qualification remain pilot-only and must stay isolated from real research projects until the pilot is formally closed. Keep important workloads supervised until the remaining physical failure/recovery gates are closed.

## 8. Pilot identities versus production project roots

The current physical V2-BD qualification uses disposable roots under:

```text
%USERPROFILE%\RemoteMCP-V2BD-Pilot\workspace
```

Those identities are evidence fixtures, not production project hosts. Do not bind real research repositories into those pilot roots before qualification closes.

For a real deployment, choose a node root that contains the projects that node is allowed to execute. The generated one-file join flow currently defaults to:

```text
%USERPROFILE%\RemoteMCP-Workspace
```

Projects must live under the configured node root. If existing repositories live elsewhere, either place/clone them under the production node root or use an explicit production pairing configuration with the desired `--root`.

## 9. Recommended ChatGPT operating sequence

For multi-device work, the assistant should follow this order:

```text
device_list / device_status
        |
        v
project_register_on_device
        |
        v
task_create
        |
        v
task_claim
        |
        v
task-scoped read / CAS / job tools
```

For a new project, tell ChatGPT which machine should own it. After that, the task binding should be treated as authoritative.

## 10. Security notes

- Do not commit OAuth state, pairing secrets, node private keys, or runtime databases.
- Pairing credentials are short-lived and single-use.
- Node private keys stay on the execution machine.
- Execution nodes use outbound HTTPS only.
- A project/task binding is not a load-balancing hint; it is an execution-placement invariant.
- Revocation and route-generation errors fail closed.

## 11. ChatGPT plugin setup

See [CHATGPT_PLUGIN_SETUP.md](CHATGPT_PLUGIN_SETUP.md) for the generated private-plugin package and schema-refresh guidance.


## Operator commands in ChatGPT

The staged ChatGPT plugin package includes operator command files:

```text
/status
/devices
/restart <device>
```

### `/status`

Read-only. It inspects gateway-visible execution devices using `device_list` and `device_status`, then reports device state, hostname, route generation, project count, active commands, and active routed jobs.

### `/devices`

Read-only device inventory. It calls `device_list` once and lists every gateway-visible execution device, including ONLINE/OFFLINE/REVOKED state, hostname, `device_id`, route generation, bound projects, active commands, and active routed jobs when present.

If `device_list` times out, the command reports gateway/transport unavailability; it must not reinterpret a timeout as an empty registry.

There is no `/deviceList` alias. Use `/devices`.

### `/restart <device>`

Restarts exactly one execution node through the routed `device_restart` control operation.

Safety rules:

- when more than one device exists, the command must resolve an explicit device id, device name, or hostname; it must not guess;
- if the target has active routed jobs, restart is blocked unless the user explicitly confirms interruption;
- after restart, the same `device_id`, key fingerprint, and route generation must return;
- pairing a replacement device is not a restart fallback.

Example:

```text
/restart physical-machine-1
```

The command is **in-band**: it works only while the RemoteMCP gateway/MCP endpoint itself is responding.

### What if the entire RemoteMCP endpoint is timing out?

Do not try to repair a dead gateway through `/restart`. If unrelated MCP tools such as `device_list`, `project_status`, and `list_dir` all time out, use the out-of-band gateway recovery path on the gateway host:

```powershell
.\Test-RemoteMCP-Gateway.ps1
.\Start-RemoteMCP-Gateway.ps1
```

For unattended recovery, install the watchdog:

```powershell
.\Install-RemoteMCP-Gateway-Watchdog.ps1 -StartNow
```

See [Gateway recovery and watchdog](GATEWAY_RECOVERY.md).

### Current deployment boundary

These operator commands and the `device_restart` backend are currently staged on `main`. They must not be described as live on the deployed production connector until the corresponding source release and plugin package have been deployed/refreshed.


## Private deployment configuration

RemoteMCP is a public repository, so deployment-specific values must stay local.

Copy:

```text
.env.example -> .env
```

The local `.env` file is gitignored. Use it for non-secret deployment metadata such as:

```text
PUBLIC_URL
PORT
MCP_ROOT
MCP_STATE
MCP_RUNTIME_DIR
ALLOWED_REDIRECT_HOSTS
REMOTEMCP_PILOT_HOST_A
REMOTEMCP_PILOT_HOST_B
REMOTEMCP_QUAL_WORKSPACE
REMOTEMCP_QUAL_LLAMA_CLI
```

Do not put `OWNER_PASSWORD`, OAuth tokens, pairing codes, node private keys, or other credentials in `.env`. On Windows, `Configure-RemoteMCP-Gateway.ps1` persists the owner password separately using DPAPI.

Public documentation and qualification records should use placeholders rather than real domains, hostnames, device/session IDs, or local filesystem paths.
