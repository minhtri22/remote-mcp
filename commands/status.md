---
description: Show a read-only RemoteMCP health summary and highlight devices that need attention.
---

# RemoteMCP Status

## Preflight

Use RemoteMCP MCP tools only. Do not use local shell output as a substitute for gateway-visible state.

## Plan

1. Call `device_list`.
2. Summarize whether the gateway responded and how many execution devices are visible.
3. Highlight devices that are OFFLINE, REVOKED, carrying active commands, or carrying active routed jobs.
4. Use `device_status` only when a device needs detail beyond the inventory returned by `device_list`.

## Commands

This command is read-only.

Use:

```text
device_list
device_status(device_id)   # only when useful for detail
```

Do not mutate device, project, task, or job state.

## Verification

At least one live MCP response must succeed. Cached identity metadata is not sufficient evidence that the gateway is healthy.

If the MCP endpoint itself times out, report that the status command could not obtain live gateway evidence. Do not pair devices or create replacement project registrations.

## Summary

Return a compact health summary:

```text
Gateway: reachable | unavailable
Devices: <count>
Attention: <none | concise list of offline/busy/revoked devices>
```
