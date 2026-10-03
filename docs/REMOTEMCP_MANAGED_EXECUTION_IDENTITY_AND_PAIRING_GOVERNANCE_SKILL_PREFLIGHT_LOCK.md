# RemoteMCP managed execution identity and pairing governance skill preflight lock

Date: 2026-10-03

Gate:

`REMOTEMCP_MANAGED_EXECUTION_IDENTITY_AND_PAIRING_GOVERNANCE_SKILL_PREFLIGHT_LOCK`

Status:

`PASS — GOVERNANCE SKILL PREFLIGHT LOCKED`

## Problem

Remote execution agents can encounter project-root mismatch, connector/session failures, gateway failures, node-process failures, and lost scientific-job observability. Those are distinct failure domains.

Without an explicit preflight contract, an agent can incorrectly treat a pairing ticket as authorization, create a new device identity to repair project exposure, broaden a node root, route a pairing secret through a job, restart a healthy node for a connector failure, or relaunch a scientific run whose status is merely temporarily unobservable.

## Governance boundary

This gate is infrastructure governance. It does not alter scientific contracts and must not be appended to scientific `LINEAGE.md`.

The gate does not pair, re-pair, revoke, restart, register, bind, or relaunch anything.

## Source-of-truth skill

The agent skill is now a real source file:

`skills/remote-mcp/SKILL.md`

The plugin builder must package that exact file rather than embedding a duplicate string.

The machine-readable companion contract is:

`skills/remote-mcp/managed_execution_policy.json`

Both Desktop direct-MCP and Web app-backed packages must include the exact audited skill and policy bytes.

## Mandatory preflight invariants

Before any pairing, re-pairing, revocation, replacement, restart, project registration/binding, or scientific job relaunch:

1. inspect device state read-only first;
2. preserve an existing approved execution identity;
3. classify connector/session, gateway/control-plane, node-process, project-root, and scientific-observability failures separately;
4. treat a pairing ticket as capability material, not authorization;
5. never repair a project-root mismatch by auto-pairing, root broadening, implicit project migration, or identity replacement;
6. require an explicit dedicated-node gate with exact device role/name, exact project root, and exact runtime directory;
7. never broaden an approved project root to a parent directory;
8. never transport pairing secrets through routed jobs, shell/job arguments, task payloads, repo files, logs, checkpoints, or lineage;
9. never use a scientific/research task as infrastructure transport;
10. never treat observability loss as authorization to resubmit a scientific job;
11. never treat connector/OAuth/gateway failure as evidence of node-process failure;
12. fail closed when identity, scope, secure pairing channel, or authorization is ambiguous.

## Executable preflight

`scripts/managed_execution_preflight.py` provides a deterministic fail-closed evaluator for the governance facts available before a mutating action.

Expected blocked decisions include:

- `BLOCKED_ON_EXISTING_IDENTITY_PRESERVATION`
- `BLOCKED_ON_EXPLICIT_DEDICATED_NODE_GATE`
- `BLOCKED_ON_DEDICATED_NODE_SCOPE`
- `BLOCKED_ON_SECURE_PAIRING_CHANNEL`
- `BLOCKED_ON_MANAGED_PROJECT_EXPOSURE_OR_EXPLICIT_DEDICATED_NODE_GATE`
- `BLOCKED_ON_NODE_RESTART_NOT_JUSTIFIED`
- `BLOCKED_ON_SCIENTIFIC_JOB_RELAUNCH_AUTHORIZATION`

The evaluator never consumes a pairing secret and never performs a mutation.

## PASS criteria

This gate may be adjudicated PASS only when:

- governance evaluator tests pass on Windows and Linux;
- plugin builder tests prove Desktop and Web packages contain the exact audited skill and policy;
- existing Web app-backed identity freeze tests remain PASS;
- public repository hygiene remains PASS;
- exact source/test/workflow hashes are frozen;
- no execution device, project, task, job, or production gateway is mutated by this gate.

Only after PASS may the already-open node supervisor maintenance deployment/drill continue.


## Executable QA lock

GitHub Actions workflow:

- name: `Managed execution governance preflight QA`
- run id: `37134310904`
- Windows conclusion: `success`
- Ubuntu conclusion: `success`
- Windows targeted suite: `31 passed`
- Ubuntu targeted suite: `31 passed`

Frozen source identities:

- `skills/remote-mcp/SKILL.md`: `eadf3109611a8aadbf6731f3d177b1d96c777729`
- `skills/remote-mcp/managed_execution_policy.json`: `8287dbe3a1a6e8ceb036ce2d4c818405cf105116`
- `scripts/managed_execution_preflight.py`: `348827da50433ad909cf27cb224f9eb3caef58f6`
- `scripts/build_chatgpt_plugin.py`: `44ccb353d82e97f49005222a305c42f11463d30e`
- `tests/test_managed_execution_governance.py`: `1fce6b25eea0be62ba0dad492debb16e88bc0f64`
- `tests/test_chatgpt_plugin_builder.py`: `cce1f17ab177d87e1c8581d6c7d749a0fe7865dc`
- `.github/workflows/managed-execution-governance-qa.yml`: `791f06143ae78a96e98df86c8c4c31e498e927ba`

The QA proved that both Desktop direct-MCP and Web app-backed package targets contain the exact audited skill and machine-readable policy, while the Web app-backed identity guard still refuses a Desktop target.

Any change to the frozen skill, policy, evaluator, plugin builder, or governance tests requires this gate's QA again.

No production gateway, device identity, project, task, routed job, or scientific run was mutated while establishing this lock.

## Gate adjudication

`REMOTEMCP_MANAGED_EXECUTION_IDENTITY_AND_PAIRING_GOVERNANCE_SKILL_PREFLIGHT_LOCK = PASS`

The next valid infrastructure gate is:

`REMOTEMCP_MACHINE1_SELF_HEALING_NODE_SUPERVISOR_MAINTENANCE_DEPLOYMENT_AND_CONTROLLED_RECOVERY_DRILL`
