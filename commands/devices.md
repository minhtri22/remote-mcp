---
description: List all RemoteMCP execution devices and their current gateway-visible routing state.
---

# RemoteMCP Devices

## Preflight

This command is read-only. Use the gateway device inventory; do not infer devices from prior conversation state, local process lists, or cached identities.

## Plan

1. Call `device_list` exactly once.
2. Present every returned execution device.
3. Preserve the gateway-reported device identity and routing metadata.
4. Do not mutate any device or project state.

## Commands

Call:

```text
device_list
```

Display, when present:

- device name;
- hostname;
- `device_id`;
- state: ONLINE / OFFLINE / REVOKED;
- route generation;
- bound project count;
- active command count;
- active routed job count.

If no devices are returned, state that the gateway currently reports no paired execution devices.

## Verification

The list must come from a live `device_list` response.

Do not silently omit OFFLINE or REVOKED devices. Do not translate one device into another identity.

If `device_list` times out, report gateway/transport unavailability rather than claiming the device registry is empty.

## Summary

Prefer a compact table such as:

```text
NAME   HOSTNAME   DEVICE_ID   STATE   GEN   PROJECTS   COMMANDS   JOBS
```

Do not create or document a `/deviceList` alias. The canonical command is `/devices`.
