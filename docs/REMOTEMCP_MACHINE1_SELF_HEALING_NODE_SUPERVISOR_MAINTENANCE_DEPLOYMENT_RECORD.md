# RemoteMCP machine-1 self-healing node supervisor — maintenance deployment record

Date: 2026-10-03

Gate:

`REMOTEMCP_MACHINE1_SELF_HEALING_NODE_SUPERVISOR_MAINTENANCE_DEPLOYMENT`

Verdict:

`PASS — DEPLOYED / CONTROLLED RECOVERY DRILL NOT YET AUTHORIZED`

## Deployed source

Supervisor source snapshot commit:

`c1550f4f4973512a7bafb3db063fde65c3d68598`

Frozen relevant blobs:

- `Watch-RemoteMCP-Node.ps1`: `b103826237cf5b89493aeb80d865025a8f7425a6`
- `Install-RemoteMCP-Node-Supervisor.ps1`: `b9d5c206783dc3e2320b78f787465c0ba673c5f6`
- `Start-RemoteMCP-Node.ps1`: `a70934d9f22bd14208c2c631f7f5ffe8e107071f`

The deployment used a full immutable repository snapshot so the recovery starter has a valid RemoteMCP node source tree.

## Persistence result

Windows Scheduled Task registration was denied by host policy.

The hardened installer therefore used the approved current-user Startup fallback and emitted:

- `Persistence : Startup`
- `REMOTEMCP_NODE_SUPERVISOR_INSTALL=PASS`

The watchdog process started successfully.

## Logical process topology

The existing exact-runtime node appeared as two raw Windows processes because the virtual-environment launcher is the parent of the base Python interpreter.

The logical-process-group gate had already adjudicated that topology as one logical node root. Installation recognized one logical root and did not restart, stop, or replace the execution node.

## Identity and continuity verification

Post-install live control-plane checks established:

- device listing succeeded;
- multiple read-only device-status calls succeeded;
- the execution node remained ONLINE;
- device identity, key fingerprint, and route generation remained unchanged;
- a pre-existing routed job remained readable with the same task/job/device identities and terminal SUCCEEDED state;
- no scientific job was resubmitted or relaunched.

The watchdog startup log was created successfully.

## Controlled recovery boundary

The controlled process-only recovery drill is intentionally not executed by this deployment adjudication.

At post-install verification time, the device reported more than fifty active routed jobs, with the observed count changing between reads. Under the managed-execution governance lock, active routed jobs require explicit disruption confirmation before any node-process recovery drill.

Therefore:

`REMOTEMCP_MACHINE1_SELF_HEALING_NODE_SUPERVISOR_MAINTENANCE_DEPLOYMENT = PASS`

and:

`REMOTEMCP_MACHINE1_SELF_HEALING_NODE_SUPERVISOR_CONTROLLED_RECOVERY_DRILL = BLOCKED_ON_ACTIVE_ROUTED_JOBS_CONFIRMATION`

This is infrastructure/control-plane evidence and must not be appended to scientific `LINEAGE.md`.
