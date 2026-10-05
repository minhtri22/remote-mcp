# RemoteMCP V2 Web canonical plugin identity and session-binding preflight

Date: 2026-10-04

Gate:

`REMOTEMCP_V2_WEB_CANONICAL_PLUGIN_IDENTITY_AND_SESSION_BINDING_PREFLIGHT`

Status:

`PASS — CANONICAL WEB IDENTITY AND SESSION-BINDING PREFLIGHT LOCKED`

## Trigger

A ChatGPT Web conversation reported all of the following before reaching the execution control plane:

- `device_list` failed because the selected `link_id` did not identify an eligible linked account;
- `device_status(machine-1)` failed for the same reason;
- the conversation reported that `RemoteMCP V2 Web` was not installed.

Independent live checks from another healthy conversation established that the RemoteMCP gateway and execution node remained available.

## Root-cause boundary

This failure class is defined as:

`PLUGIN_SESSION_BINDING_FAILURE`

It covers stale or missing ChatGPT plugin attachment, stale private-plugin release state in an existing conversation, ambiguous Web plugin identity, and account-link resolution failures before the gateway/node execution plane is reached.

It is not evidence of:

- execution-node failure;
- gateway failure;
- project-root failure;
- need for pairing/re-pairing;
- need for a replacement project/task;
- authorization to relaunch a scientific job.

## Canonical identity

The canonical ChatGPT Web private plugin is:

`remote-mcp-v2-web-clean`

The older plugin identity:

`remote-mcp-v2-web`

is legacy direct-MCP and is not an eligible canonical Web route.

Canonical and legacy display names must be visibly distinct.

The canonical package is app-backed and must contain `.app.json` while containing neither `.mcp.json` nor `mcp.json`.

## Live preflight evidence before mutation

Read-only live checks established that the two currently exposed RemoteDesktop linked-account handles both resolve successfully and both can execute `device_list` against the same live RemoteMCP control plane.

Therefore the screenshot failure is not explained by a globally broken account link. It is consistent with conversation/session attachment or link-resolution state local to that ChatGPT thread.

Private plugin inspection also established:

- the clean Web plugin exists and is app-backed;
- the legacy Web plugin still exists and retains legacy direct-MCP files;
- before this gate, both packages used the same user-facing display name, creating avoidable resolver ambiguity.

No private link IDs, app IDs, device IDs, or local secrets are stored in this public record.

## Governance changes

The skill and machine-readable policy now require:

1. classify missing/unavailable plugin or ineligible linked account as `PLUGIN_SESSION_BINDING_FAILURE`;
2. do not pair/re-pair, revoke/replace identity, restart the node, widen roots, register replacement projects, or relaunch scientific jobs as remediation;
3. use `remote-mcp-v2-web-clean` as the canonical Web identity;
4. never fall back to `remote-mcp-v2-web` as the canonical route;
5. use a fresh conversation for the final session smoke test after a private plugin release change.

The executable preflight has a dedicated `chatgpt-web-session-binding` action that fails closed until canonical plugin availability, current-conversation attachment, app-backed dependency, and an eligible linked account are all established.

## PASS criteria

This gate may be adjudicated PASS only when:

- Windows and Linux governance/package QA pass;
- the canonical source builder refuses direct-MCP packaging for the clean Web identity;
- the builder refuses the legacy Web identity as a canonical package;
- the builder refuses the old ambiguous canonical display name;
- canonical and legacy live private plugins have distinct display names;
- the clean live plugin remains app-backed and has no direct-MCP files;
- read-only live `device_list` and `device_status` succeed through an eligible linked account after propagation;
- the remaining limitation of existing-conversation freshness is stated explicitly.

No gateway/node restart, pairing, project mutation, or scientific job mutation belongs to this gate.


## Executable QA lock

GitHub Actions run:

`37139777440`

Results:

- Ubuntu governance/package/public-hygiene suite: `38 passed`
- Windows governance/package/public-hygiene suite: `38 passed`

Frozen source identities:

- `scripts/build_chatgpt_plugin.py`: `af219d1251216c493fd21e40513e3c33df94f30c`
- `skills/remote-mcp/SKILL.md`: `299158fdefb514002d871c5e0b5f908c018106c0`
- `skills/remote-mcp/managed_execution_policy.json`: `74eaf19179d48028462203fadc04da5a061a8449`
- `scripts/managed_execution_preflight.py`: `9fd66928707e6f6e2576ecb3a0c464d5ca22fbfd`
- `tests/test_chatgpt_plugin_builder.py`: `e7ef72a9b40edafa9152b34333588aaf36881e1c`
- `tests/test_managed_execution_governance.py`: `254f8abba436aef36283b41cd6b41a3132b650a7`
- `.github/workflows/managed-execution-governance-qa.yml`: `ca5e52d2ef6b5aa1a0df8496451952ee039c0452`

Source lock merge commit:

`4e6316d3e40533e287ab8090821722ffebfffa46`

## Live private-package propagation

Canonical Web package:

- identity: `remote-mcp-v2-web-clean`
- version: `1.5.2`
- display name: `RemoteMCP V2 Web (Canonical)`
- transport: app-backed
- package contains `.app.json`
- package does not contain `.mcp.json`
- package does not contain `mcp.json`

Legacy package:

- identity: `remote-mcp-v2-web`
- version: `1.4.3`
- display name: `RemoteMCP V2 Web (Legacy Direct)`
- legacy direct-MCP files remain present by design
- it is explicitly non-canonical

Both packages carry the same locked session-binding governance skill and machine-readable policy.

## Live read-only smoke after propagation

After the private-package updates:

- both currently exposed linked-account handles successfully executed `device_list`;
- both observed the same live RemoteMCP control plane;
- `device_status` for the established execution node succeeded;
- the execution node remained ONLINE with unchanged route generation;
- no pairing, node restart, project mutation, task mutation, or scientific job relaunch was performed.

This proves the underlying app connection and linked-account eligibility are healthy at gate close.

## Fresh-conversation limitation

An already-open ChatGPT conversation can retain stale plugin/tool attachment state after a private plugin release changes.

This gate cannot turn the current conversation into a newly created conversation, so it does not claim that the exact stale thread which triggered the investigation has refreshed its UI attachment cache.

The operational rule is therefore locked prospectively:

- in a fresh conversation, select `RemoteMCP V2 Web (Canonical)`;
- if the canonical plugin is unavailable/not installed or an eligible linked account cannot be resolved, classify the condition as `PLUGIN_SESSION_BINDING_FAILURE`;
- do not remediate that condition by pairing, node restart, root widening, replacement project registration, or scientific-job relaunch.

## Gate adjudication

`REMOTEMCP_V2_WEB_CANONICAL_PLUGIN_IDENTITY_AND_SESSION_BINDING_PREFLIGHT = PASS`

This is infrastructure/control-plane evidence and must not be appended to scientific `LINEAGE.md`.
