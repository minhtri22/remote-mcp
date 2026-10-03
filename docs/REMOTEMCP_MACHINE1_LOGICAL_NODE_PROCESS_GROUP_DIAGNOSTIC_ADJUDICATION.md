# RemoteMCP logical node process-group diagnostic adjudication

Date: 2026-10-03

Gate:

`REMOTEMCP_MACHINE1_DUPLICATE_EXACT_RUNTIME_NODE_PROCESS_DIAGNOSTIC_AND_NONDISRUPTIVE_ADJUDICATION`

Status:

`SOURCE-STAGED — QA REQUIRED — NO PROCESS MUTATION`

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
