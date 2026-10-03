# RemoteMCP V2 Web canonical plugin identity and session-binding preflight

Date: 2026-10-04

Gate:

`REMOTEMCP_V2_WEB_CANONICAL_PLUGIN_IDENTITY_AND_SESSION_BINDING_PREFLIGHT`

Status:

`SOURCE-STAGED — CROSS-PLATFORM QA AND LIVE PACKAGE PROPAGATION REQUIRED`

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
