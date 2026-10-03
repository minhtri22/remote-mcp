# RemoteMCP node supervisor deployment compatibility recovery preflight

Date: 2026-10-03

Gate context:

`REMOTEMCP_MACHINE1_SELF_HEALING_NODE_SUPERVISOR_MAINTENANCE_DEPLOYMENT_AND_CONTROLLED_RECOVERY_DRILL`

Recovery sub-gate:

`REMOTEMCP_MACHINE1_SELF_HEALING_NODE_SUPERVISOR_DEPLOYMENT_COMPAT_RECOVERY_PREFLIGHT`

Status:

`PASS — RECOVERY PREFLIGHT LOCKED / NOT DEPLOYED`

## Trigger

The first supervisor installation attempt preserved the existing node process and identity but did not install persistence.

Observed deployment findings:

1. Windows Task Scheduler registration returned access denied for the current user context.
2. The v1 installer incorrectly continued after that non-terminating cmdlet error and printed success-like messages even though no Scheduled Task existed.
3. The materialized supervisor directory contained only the three scripts, while the recovery starter requires a valid RemoteMCP node source tree containing `remotemcp/node/__main__.py`.
4. Read-only process inspection before installation found more than one node process using the exact same paired runtime. No process was killed or restarted.

## v2 fail-safe requirements

The revised installer/watchdog must:

- treat Scheduled Task registration errors as terminating failures;
- in `Auto` mode, fall back to a current-user Startup entry without requiring administrator rights;
- print `REMOTEMCP_NODE_SUPERVISOR_INSTALL=PASS` only after persistence is verifiably materialized;
- accept an explicit `NodeSourceDir` distinct from the watchdog script directory;
- require the node source tree to contain `remotemcp/node/__main__.py`;
- call the state-preserving starter with that exact `NodeSourceDir`;
- refuse installation unless exactly one existing process matches the exact paired runtime;
- make the watchdog safety-stop if multiple exact-runtime node processes are later observed;
- never pair, revoke, replace, or widen the node identity/root;
- never use connector/gateway health as a restart trigger;
- never relaunch scientific jobs.

## Deployment boundary

Do not reinstall persistence until the current duplicate-process condition is diagnosed read-only.

Do not kill either node process while active routed work may exist unless a separate controlled-recovery authorization explicitly allows disruption.

After the duplicate-process condition is resolved, materialize a full immutable RemoteMCP source snapshot for the supervisor so recovery never depends on a mutable working tree.

## PASS criteria

This recovery preflight is PASS only after:

- PowerShell parse QA passes;
- source separation and duplicate-process fail-closed tests pass;
- Scheduled Task failure handling and Startup fallback are covered by static contracts;
- existing node starter and public-repository hygiene tests pass;
- exact source/test/workflow hashes are frozen.

No production process mutation is part of this sub-gate.


## Executable QA lock

GitHub Actions run: `37135395855`

Observed:

- PowerShell parse preflight: PASS
- exact-runtime/source wiring PlanOnly preflight: `REMOTEMCP_NODE_SUPERVISOR_PLAN_ONLY=PASS`
- per-user Startup-mode PlanOnly construction: `REMOTEMCP_NODE_SUPERVISOR_PLAN_ONLY=PASS`
- targeted supervisor + starter + public-hygiene tests: `9 passed`

Frozen source identities:

- `Watch-RemoteMCP-Node.ps1`: `1f135c84d159f9b1a189d0eb78ed57282ef74fba`
- `Install-RemoteMCP-Node-Supervisor.ps1`: `eb59da19c407db578800897bd728fd89f4a7a8a1`
- `tests/test_node_supervisor_scripts.py`: `9425c6cfd3d65dcfeb40140699fdf8267a8306bf`
- `.github/workflows/node-supervisor-static-qa.yml`: `f29cddb11ee9f3e64cf14cc5d5132aa8e27d2803`

The v1 deployment attempt created no Scheduled Task and no watchdog process. The existing execution node process set was unchanged by that failed installation attempt.

## Gate adjudication

`REMOTEMCP_MACHINE1_SELF_HEALING_NODE_SUPERVISOR_DEPLOYMENT_COMPAT_RECOVERY_PREFLIGHT = PASS`

The maintenance deployment remains blocked until the duplicate exact-runtime node-process condition is diagnosed and reduced to an unambiguous single process without violating active-job continuity.
