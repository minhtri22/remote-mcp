# RemoteMCP machine-1 self-healing node supervisor — Static preflight and deployment plan

Date: 2026-10-03

Gate:

`REMOTEMCP_MACHINE1_SELF_HEALING_NODE_SUPERVISOR_STATIC_PREFLIGHT_AND_DEPLOYMENT_PLAN`

Verdict:

`SOURCE-STAGED — STATIC QA REQUIRED — NOT DEPLOYED`

## Problem being addressed

The execution node already retries transient transport failures inside `NodeService.run_forever()` with bounded reconnect backoff. A supervisor therefore must **not** restart the node because the public gateway, tunnel, OAuth path, or ChatGPT connector is temporarily unavailable.

The missing recovery layer is process supervision: if the node process itself exits, there is no independent long-lived supervisor that restores the same paired runtime automatically.

## Frozen operational invariants

The supervisor must:

1. require an explicit paired `RuntimeDir`; never auto-select from multiple runtimes;
2. snapshot and continuously verify `device_id`, key fingerprint, and route generation;
3. monitor only the process whose command line contains the exact `--runtime-dir`;
4. start the node only after the exact process is absent for a bounded threshold;
5. call the existing `Start-RemoteMCP-Node.ps1` **without** `-Restart`;
6. never kill a live node process;
7. never pair, revoke, rotate, or replace a device identity;
8. never use `device_list`, `device_status`, OAuth health, tunnel health, or Internet reachability as a restart trigger;
9. never resubmit or relaunch scientific jobs;
10. fail closed if the paired identity changes underneath the supervisor.

## Implementation staged

Files:

- `Watch-RemoteMCP-Node.ps1`
- `Install-RemoteMCP-Node-Supervisor.ps1`
- `tests/test_node_supervisor_scripts.py`
- `.github/workflows/node-supervisor-static-qa.yml`

The watcher uses an exact-runtime process probe and a missing-process threshold. The installer creates a per-user Windows Scheduled Task with:

- logon trigger;
- limited/current-user principal;
- `IgnoreNew` multiple-instance policy;
- Task Scheduler restart-on-supervisor-failure;
- no node restart at installation unless `-StartNow` is explicitly requested.

## Deployment plan — not executed by this gate

During an explicit deployment window:

1. verify the intended runtime contains the existing paired identity and private key;
2. record the current device identity, fingerprint, route generation, and live `device_status`;
3. verify the exact node process for the runtime is already healthy;
4. run `Install-RemoteMCP-Node-Supervisor.ps1 -RuntimeDir <RUNTIME_DIR> -StartNow`;
5. verify the scheduled task is running;
6. verify the existing node process was not killed or replaced;
7. verify live `device_status` still reports the same identity and route generation;
8. perform one controlled **process-only** recovery drill only in a separately authorized maintenance window; do not use a scientific job as the drill;
9. confirm the supervisor restores the same runtime and identity;
10. leave scientific task/job identities untouched.

## Rollback plan

Rollback removes or disables only the supervisor scheduled task and stops only the watchdog process/task instance. It must not stop, re-pair, revoke, or mutate the execution node identity.

## Static PASS criteria

This gate may become PASS only after:

- PowerShell parse checks pass for both scripts;
- targeted supervisor contract tests pass;
- existing node starter contract tests pass;
- public-repository hygiene tests pass;
- source hashes are frozen;
- no installation or node restart occurred during static QA.

## Scientific boundary

This is infrastructure reliability work. Do not append it to any research `LINEAGE.md`.
