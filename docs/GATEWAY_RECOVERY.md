# RemoteMCP Gateway Recovery

RemoteMCP has two distinct recovery planes.

## 1. In-band execution-node recovery

When the gateway is healthy and MCP tools respond, ChatGPT can use the routed node control surface.

The staged source adds:

```text
device_restart(operation_id, device_id, reason, allow_active_jobs=false)
```

The ChatGPT plugin package also includes operator command files:

```text
/status
/restart <device>
```

`/restart <device>` must not guess between multiple devices. If the target has active routed jobs, restart is blocked unless the user explicitly confirms interruption.

## 2. Out-of-band gateway recovery

If all MCP tools time out, the plugin cannot repair the gateway through the same dead endpoint.

Symptoms include simultaneous timeouts from unrelated tools such as:

```text
device_list
project_status
device_status
agent_register
list_dir
```

In that state:

- do not pair a replacement execution device;
- do not create duplicate project registrations;
- do not rotate node identity;
- recover the gateway locally on the gateway host.

The repository root provides:

```text
Configure-RemoteMCP-Gateway.ps1
Test-RemoteMCP-Gateway.ps1
Start-RemoteMCP-Gateway.ps1
Restart-RemoteMCP-Gateway.ps1
Watch-RemoteMCP-Gateway.ps1
Install-RemoteMCP-Gateway-Watchdog.ps1
```

## One-time recovery configuration

Run once on the gateway Windows account:

```powershell
.\Configure-RemoteMCP-Gateway.ps1
```

The configuration stores only non-secret gateway paths/settings in:

```text
%LOCALAPPDATA%\RemoteMCP\gateway-config.json
```

The owner password is stored separately using Windows DPAPI via `ConvertFrom-SecureString`:

```text
%LOCALAPPDATA%\RemoteMCP\gateway-owner-password.txt
```

The encrypted secret is tied to the current Windows user. OAuth state and RemoteMCP durable runtime remain at the explicitly configured existing paths.

## Read-only diagnosis

```powershell
.\Test-RemoteMCP-Gateway.ps1
```

This checks:

- whether the configured port has a listener;
- listener PID/command line;
- local OAuth metadata health.

A listener alone is not considered healthy.

## Triage: local gateway vs public tunnel vs ChatGPT connector

Run local health first:

```powershell
.\Test-RemoteMCP-Gateway.ps1
```

Then, if local health is true, probe the public metadata URL:

```powershell
Invoke-WebRequest -UseBasicParsing -Uri "$env:PUBLIC_URL/.well-known/oauth-authorization-server" -TimeoutSec 10
```

Interpretation:

| Local metadata | Public metadata | ChatGPT tools | Likely layer | Action |
| --- | --- | --- | --- | --- |
| fail | fail/timeout | timeout | gateway process/runtime | out-of-band gateway recovery |
| pass | fail/timeout | timeout | tunnel / DNS / edge | repair tunnel/edge; do not restart a healthy gateway repeatedly |
| pass | pass | timeout | connector/OAuth/client path | refresh/reconnect plugin; do not pair devices or duplicate projects |
| pass | pass | pass | healthy | no recovery action |

## Manual recovery

Start if stopped, or recover if the listener is unhealthy:

```powershell
.\Start-RemoteMCP-Gateway.ps1
```

Force a deliberate gateway restart:

```powershell
.\Restart-RemoteMCP-Gateway.ps1
```

Safety rules:

- a process is killed only when the target port is owned by a process whose command line looks like `server.py`;
- the configured `MCP_STATE` and `MCP_RUNTIME_DIR` are reused;
- the script does not pair, revoke, or recreate execution-device identities;
- recovery verifies local OAuth metadata before declaring the gateway online.

Execution nodes are expected to reconnect automatically after a gateway restart.

## Automatic watchdog

Install a per-user Startup watchdog:

```powershell
.\Install-RemoteMCP-Gateway-Watchdog.ps1 -StartNow
```

The watchdog performs local health checks. By default, three consecutive failures trigger out-of-band gateway recovery.

Default timing:

```text
health interval      15 s
health timeout        5 s
failure threshold     3
```

The watchdog log is written to:

```text
%LOCALAPPDATA%\RemoteMCP\gateway-watchdog.log
```

## Important boundary

A ChatGPT slash command cannot recover a gateway that is already unable to answer MCP requests. The watchdog/local PowerShell recovery path exists specifically to remove that circular dependency.

`/restart <device>` is for an execution node while the gateway is alive.

Gateway recovery is out-of-band.

## Active scientific workloads

An availability recovery is different from a qualification failure-injection gate.

If a scientific workload already lost gateway service because of an incident, restoring the gateway is allowed operational recovery. Do not intentionally stop/revoke/restart execution nodes merely to qualify failure behavior while another experiment requires continuous two-machine connectivity.
