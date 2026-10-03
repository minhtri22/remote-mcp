# RemoteMCP logical node process-group diagnostic adjudication

Date: 2026-10-03

Gate:

`REMOTEMCP_MACHINE1_DUPLICATE_EXACT_RUNTIME_NODE_PROCESS_DIAGNOSTIC_AND_NONDISRUPTIVE_ADJUDICATION`

Status:

`PASS — LOGICAL PROCESS-GROUP ADJUDICATION LOCKED`

## Read-only observation

The exact-runtime process probe reported two command-line matches. Process-tree inspection showed that they were not two independent execution nodes:

- one match was the virtual-environment Python launcher;
- the second match was its direct child base interpreter;
- both were created at the same instant with the same node command line;
- the child interpreter carried the active CPU load and owned the routed-job child process;
- the launcher was the single matching process whose parent was outside the exact-runtime match set.

Therefore raw process count is not a valid duplicate-node detector on this Windows Python environment.

## Adjudication

The observed topology is one logical node process group, not two independent node identities or two independent node roots.

No process is to be killed, restarted, paired, revoked, or replaced as part of this diagnostic.

## Supervisor correction

The supervisor now computes logical roots from the exact-runtime process set:

- a matching process whose parent is another matching process is a descendant in the same logical node group;
- exactly one logical root is healthy even when the virtual-environment launcher and base interpreter are both visible;
- zero logical roots means the node is absent;
- more than one logical root means independent duplicate execution and remains fail-closed.

This rule is used both at install time and continuously by the watchdog.

## PASS boundary

This gate may become PASS only after Windows static QA and regression tests confirm that a parent-child launcher chain collapses to one logical root while two independent process chains remain two roots.

This is infrastructure evidence and is not scientific lineage.


## Executable QA lock

GitHub Actions run: `37135750064`

Observed:

- PowerShell parse preflight: PASS
- Scheduled Task PlanOnly construction: PASS
- per-user Startup PlanOnly construction: PASS
- targeted supervisor/starter/public-hygiene suite: `11 passed`

Frozen source identities:

- `Watch-RemoteMCP-Node.ps1`: `b103826237cf5b89493aeb80d865025a8f7425a6`
- `Install-RemoteMCP-Node-Supervisor.ps1`: `b9d5c206783dc3e2320b78f787465c0ba673c5f6`
- `tests/test_node_supervisor_scripts.py`: `36dd546f5c2fb6b5ea5a4d1a413ce439367ff666`
- `.github/workflows/node-supervisor-static-qa.yml`: `f29cddb11ee9f3e64cf14cc5d5132aa8e27d2803`

Regression coverage proves that one parent/child launcher chain collapses to one logical root while two independent launcher/interpreter chains remain two roots.

## Gate adjudication

`REMOTEMCP_MACHINE1_DUPLICATE_EXACT_RUNTIME_NODE_PROCESS_DIAGNOSTIC_AND_NONDISRUPTIVE_ADJUDICATION = PASS`

The observed exact-runtime topology is one logical execution node. No process cleanup is required.

The next valid action is to resume the supervisor maintenance deployment with the hardened installer and a full immutable node source snapshot.
