# TWO_DEVICE_PRODUCTION_SESSION_REGISTRATION

Status: **PASS**
Date: 2026-09-30

Purpose: verify that one RemoteMCP owner account can be used concurrently from two physical ChatGPT login devices without last-login-wins behavior.

## Machine 2

Registered from the machine-2 ChatGPT conversation:

- client_instance_id: `physical-machine-2`
- agent_id: `agt_a68a81cc14be913daf36a847f07df4ab`
- session_id: `ses_21e642dd962508437f1b6ade821ecfb8`
- heartbeat_seq: `1`

## Machine 1

Registered from the machine-1 ChatGPT conversation:

- client_instance_id: `physical-machine-1`
- agent_id: `agt_0753c652fddee6d4b8ce01a33763d1c3`
- session_id: `ses_3311cecd6c03cd2797f52ce4bfd89d9c`
- heartbeat_seq: `1`

## Concurrent comparison

At control-plane observation time:

```text
now_ms = 1790769567558
```

Both sessions were still ACTIVE.

Shared owner account:

```text
own_6515f4627ef88ac5291180ce680c1116
```

Observed OAuth client identity was also the same on both devices:

```text
084a2c71-d7fd-4541-8f8e-466f6c7b441d
```

This confirms the production design correction: physical-device identity must not depend on distinct OAuth `auth_client_id` values. ChatGPT may share one account-level connector identity across devices.

The correct distinguishing keys are:

- `client_instance_id`
- `agent_id`
- `session_id`

Machine-2 heartbeat age at the simultaneous observation was approximately 111 seconds, still inside the frozen 120-second lease/session TTL. Its heartbeat was not refreshed from machine 1 for this proof.

## Verdict

PASS:

- same `owner_account_id`: yes;
- distinct `client_instance_id`: yes;
- distinct `agent_id`: yes;
- distinct `session_id`: yes;
- both sessions ACTIVE concurrently: yes;
- no last-login-wins invalidation: yes;
- same OAuth client across devices is tolerated: yes;
- registered V2-B projects: `0`.

No managed project was registered during this smoke test.

## Boundary

This proves multi-login-device/session support against one production RemoteMCP execution node.

It does **not** prove multi-execution-device routing. That remains the purpose of:

`REMOTE_MCP_V2BD_MULTI_DEVICE_ROUTING_PRELOCK`
