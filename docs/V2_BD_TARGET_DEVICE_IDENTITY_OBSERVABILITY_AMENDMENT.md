# V2-BD Target Device Identity Observability Amendment

Status: **FROZEN BEFORE RUNTIME PATCH**

Implementation status: **PASS / QUALIFIED 2026-10-01**

This amendment closes an observability gap found during the first real two-machine attempt.

Observed negative evidence from physical machine 2:

```text
legacy run_command
HOSTNAME=DESKTOP-4PSD0G2
```

That call still executed on the production gateway host. Therefore the currently deployed 32-tool V2-B surface is **not** evidence of multi-execution-device routing.

## Routing authority

V2-BD does **not** add `device_id` or `client_id` to legacy `run_command/read_file/write_file`.

Those compatibility tools remain gateway-local.

The explicit selection point is:

```text
device_list / device_status
        ↓
project_register_on_device(device_id, ...)
or project_bind_device(device_id, ...)
        ↓
project_device_bindings
        ↓
task_create
        ↓
immutable task_device_bindings
        ↓
task_* routed operations
```

A task-scoped operation MUST NOT accept a caller-supplied device override.

## Identity

Routing authority:

`device_id = dev_...`

Observability only:

- device name;
- node hostname;
- platform metadata.

OAuth `auth_client_id` is not execution identity. Multiple physical login machines may legitimately share the same OAuth client identity.

## Node platform reporting

Pairing and heartbeat report:

- hostname;
- operating system;
- OS release;
- machine architecture;
- Python version.

The central `devices.platform_json` stores this metadata.

`device_status` exposes both `hostname` and the full platform object.

## Routed response metadata

Every device-bound project/task operation returns a `routing` object containing at least:

```json
{
  "device_id": "dev_...",
  "device_name": "...",
  "hostname": "...",
  "device_state": "ONLINE"
}
```

When known it also includes:

- `project_id`;
- `task_id`;
- `binding_generation`.

This applies to project/task status, routed reads, CAS, and routed-job observation.

## Real two-machine acceptance

The pilot cannot PASS until all of the following are observed:

1. two distinct `dev_*` execution identities;
2. device A/B expose distinct expected physical host identity or equivalent marker evidence;
3. project A and B are explicitly bound to their own devices;
4. task A/B report the inherited device in `routing`;
5. a task-B routed job prints both its physical hostname and `REMOTEMCP_DEVICE_ID=dev_B`;
6. task A resolves to machine 1;
7. task B resolves to machine 2;
8. no task operation can override its frozen device;
9. no cross-route execution occurs.

The existing physical-machine-2 observation of `DESKTOP-4PSD0G2` through legacy `run_command` is retained as negative evidence and does not count as V2-BD success.
