# V2-BD Routing Implementation and Zero-Science Qualification

Status: **PASS**
Closed: 2026-10-01
Gate: `REMOTE_MCP_V2BD_ROUTING_IMPLEMENTATION_AND_ZERO_SCIENCE_QUALIFICATION`

This closure covers the implementation and zero-science qualification of the V2-BD routing runtime. It does **not** claim the real two-execution-machine pilot has passed, and it does **not** claim the currently deployed production release is already V2-BD.

## Delivered runtime

Gateway/control-plane:

- migration `003_v2bd.sql`;
- stable `dev_*` execution-device identities;
- one-time pairing and revoke;
- Ed25519 node authentication;
- timestamp/nonce/route-generation replay protection;
- outbound-only node long-poll transport;
- ONLINE/OFFLINE/REVOKED state machine;
- same-device durable command journal;
- project -> device binding;
- immutable task -> device inheritance;
- no silent migration/failover;
- routed job proxy/state;
- task-scoped routed reads/CAS/jobs;
- gateway-local compatibility behavior retained for legacy tools.

Execution node:

- node-local schema v1;
- stable local Ed25519 key;
- node runtime lock;
- project/task/worktree adapters;
- crash-reconcilable node CAS journal;
- V2-A durable job subruntime;
- outbound node client/runner and CLI;
- no inbound listener.

## Windows headless process execution

All RemoteMCP child-process paths use the shared Windows headless launcher contract.

Qualification on real Windows verified:

- direct child `GetConsoleWindow()==0`;
- durable payload `GetConsoleWindow()==0`;
- no visible top-level child window;
- foreground window unchanged;
- durable long payload remains observable and reaches `SUCCEEDED`.

This prevents RemoteMCP worker/job processes from opening console windows and stealing keyboard focus.

## Target-device identity observability amendment

A first physical-machine-2 check found:

```text
legacy run_command
HOSTNAME=DESKTOP-4PSD0G2
```

That is expected for the still-deployed V2-B compatibility surface: legacy `run_command` is gateway-local and is **not** evidence of V2-BD execution routing.

The amendment freezes the routing/observability model:

```text
device_list / device_status
       ↓
project_register_on_device(device_id, ...)
       ↓
project_device_bindings
       ↓
task_create
       ↓
immutable task_device_bindings
       ↓
task_* routed operations
```

Legacy `run_command/read_file/write_file` do not receive a `device_id` parameter.

Task-scoped tools do not accept caller device override. They infer the execution device from the immutable task binding.

Node pairing/heartbeat now report platform metadata including hostname. Device-bound responses expose:

```json
{
  "routing": {
    "device_id": "dev_...",
    "device_name": "...",
    "hostname": "...",
    "device_state": "ONLINE"
  }
}
```

when applicable, together with project/task/binding identifiers.

OAuth `auth_client_id` is explicitly **not** execution-device identity.

## Public MCP surface

Source qualification exposes **44 tools**:

- 12 compatibility/V2-A tools;
- 20 V2-B tools;
- 12 V2-BD tools.

The new V2-BD surface remains:

- `device_pair_begin`
- `device_list`
- `device_status`
- `device_revoke`
- `project_register_on_device`
- `project_bind_device`
- `task_list_dir`
- `task_read_file`
- `task_search`
- `task_job_get`
- `task_job_logs`
- `task_job_result`

Existing project/task/CAS/job mutation tools route internally when the task is device-bound.

## Qualification evidence

Final regression matrix:

```text
V2-BD implementation tests     30/30 PASS
V2-B regression                24/24 PASS
V2-A regression                28/28 PASS
V2-0 regression                17/17 PASS
OAuth integration              PASS / 44 tools
public tunnel smoke            PASS
git diff --check               PASS
post-implementation validator  PASS
```

Local/loopback qualification:

- pairing replay: PASS;
- signed-request nonce replay/stale timestamp/stale route generation: PASS;
- durable command replay/idempotency: PASS;
- node CAS crash/restart reconciliation: PASS;
- routed-job node-agent restart with same proxy/node IDs: PASS;
- two-node loopback routing: PASS, `no_cross_route=true`;
- Windows headless process qualification: PASS.

The loopback two-node test verifies distinct node identities/hostnames and routing metadata for project/task/read operations.

## Current production boundary

The currently deployed public production runtime remains the earlier V2-B release and still exposes the 32-tool surface.

Therefore:

- legacy `run_command` on a chat opened from physical machine 2 can still execute on the gateway host;
- this is not a V2-BD failure because the V2-BD candidate has not yet been deployed/piloted;
- no real execution device has yet been paired under the physical pilot gate;
- no research project has been bound to a V2-BD device.

## Real two-machine pilot acceptance

The next gate must use isolated pilot roots on both physical machines and must prove:

1. two distinct `dev_*` identities;
2. device A/B report the expected distinct physical hostnames or equivalent node marker;
3. project A explicitly binds to device A;
4. project B explicitly binds to device B;
5. task A/B inherit those bindings;
6. task status/read results expose matching `routing.device_id` and hostname;
7. a routed job on task B prints physical machine 2 hostname plus `REMOTEMCP_DEVICE_ID=dev_B`;
8. task A executes on physical machine 1;
9. task B executes on physical machine 2;
10. no task operation accepts a device override;
11. no cross-route execution occurs;
12. offline/reconnect/replay/restart/revoke gates remain satisfied.

The frozen launcher remains:

`scripts/qualify_v2bd_two_machine_pilot.py`

and uses only disposable isolated roots, never ArcLLM/CQG/SIX/CLDP/MindForge/research workspaces.

## Verdict

`REMOTE_MCP_V2BD_ROUTING_IMPLEMENTATION_AND_ZERO_SCIENCE_QUALIFICATION = PASS`

Implementation is qualified to open the real physical pilot. V2-BD as a whole is **not yet closed**.

## Next gate

`REMOTE_MCP_V2BD_REAL_TWO_EXECUTION_MACHINE_ISOLATED_PILOT_EXECUTION`
