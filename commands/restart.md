---
description: Restart one RemoteMCP execution node safely and verify that the same device identity returns.
---

# Restart RemoteMCP Execution Node

## Preflight

1. Call `device_list`.
2. Resolve `$ARGUMENTS` against an exact `device_id`, device name, or hostname.
3. If several devices exist and the target is not unique, ask the user to choose. Never guess.
4. Call `device_status` for the selected target.
5. If `active_routed_jobs > 0`, do not restart unless the user explicitly confirms interruption.
6. If the requested target is `gateway`, do not pretend an unresponsive gateway can restart itself through the same MCP endpoint. Use the out-of-band gateway recovery path.

## Plan

Restart exactly one execution node while preserving its existing runtime identity, key fingerprint, and route generation.

## Commands

Call `device_restart` with a fresh `operation_id`.

- Default: `allow_active_jobs=false`.
- Set `allow_active_jobs=true` only after explicit confirmation when active routed jobs exist.

After the restart is accepted, poll `device_status` until the same device returns ONLINE or the verification window is exhausted.

## Verification

PASS only if all are true:

- the selected target returns ONLINE;
- `device_id` is unchanged;
- key fingerprint is unchanged;
- route generation is unchanged.

Do not pair a replacement device as a restart fallback.

## Summary

Report:

```text
Target: <device>
Restart request: accepted | rejected
Identity preserved: yes | no | unverified
Final state: ONLINE | OFFLINE | unavailable
```
