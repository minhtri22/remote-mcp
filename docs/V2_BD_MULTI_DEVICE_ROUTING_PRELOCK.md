# REMOTE_MCP_V2BD_MULTI_DEVICE_ROUTING_PRELOCK

Status: **FROZEN / PASS**
Date: 2026-09-30
Scope: specification only. No V2-BD implementation, no real pairing, no production routing change.

Bound evidence:

- Git HEAD: `7dbb2093e1f5ef970dd8af4ebd913bf30683f40d`
- two-device login/session smoke: `specs/two_device_production_session_smoke.json`
- V2-B production release manifest: `specs/v2b_production_release_manifest.json`
- production at freeze: `127.0.0.1:8099`, PID `11372`

Machine source of truth:

- `specs/v2bd_prelock.json`
- `specs/v2bd_schema_v3.sql`
- `specs/v2bd_node_schema_v1.sql`

## Scope

V2-BD adds **execution-device routing** under the already proven single owner account.

This is different from V2-B multi-login:

```text
V2-B
one owner account
  ├── ChatGPT on machine 1
  └── ChatGPT on machine 2
           ↓
      one execution node
```

V2-BD target:

```text
ChatGPT machine 1     ChatGPT machine 2
          \             /
           same owner account
                  |
        mcp.example.com
                  |
            control plane
          /               \
      device A          device B
      workspace A       workspace B
      jobs A            jobs B
```

The login device never chooses the execution device. Routing is:

```text
project_id
   -> project_device_binding
      -> device_id
         -> task inherits device_id
            -> every task operation routes to that device
```

A task never silently migrates because the user switches laptop, chat, agent, or session.

## Transport: outbound only

Each execution device initiates an outbound HTTPS connection to the existing public origin.

No execution device needs:

- inbound public port;
- router port-forward;
- per-device Cloudflare tunnel;
- direct inbound call from the control plane.

Frozen transport:

```text
execution node
    |
    | HTTPS signed poll / heartbeat
    v
https://mcp.example.com/device/v1/...
    |
    v
central command queue
```

The public MCP endpoint remains:

`https://mcp.example.com/mcp`

Internal node endpoints:

- `POST /device/v1/pair`
- `POST /device/v1/heartbeat`
- `POST /device/v1/poll`
- `POST /device/v1/commands/{command_id}/result`

Poll long wait: 25 s.

## Device identity

Each node owns an Ed25519 key pair.

Private key:

`<MCP_NODE_RUNTIME_DIR>/device-ed25519.pem`

Identity metadata:

`<MCP_NODE_RUNTIME_DIR>/device.json`

Node database:

`<MCP_NODE_RUNTIME_DIR>/node.db`

Gateway assigns a stable:

`dev_<random>`

after pairing.

The device name is display-only. Routing never depends on hostname or display name.

The private key never leaves the node.

Observed prelock host already has `cryptography 48.0.0`, so Ed25519 is a viable implementation dependency.

## Pairing

Owner starts pairing from MCP:

`device_pair_begin(operation_id, device_name)`

It creates:

- pairing ID;
- >=144-bit URL-safe one-time code;
- 10-minute expiry.

Only SHA-256(code) is stored.

Node command:

```text
python -m remotemcp.node pair
  --url https://mcp.example.com
  --code-file <PAIRING_CODE_FILE>
  --name <DEVICE_NAME>
  --root <MCP_NODE_ROOT>
  --runtime-dir <MCP_NODE_RUNTIME_DIR>
```

The pairing-code file contains exactly one UTF-8 line. The CLI reads it once and must delete it **before** making the network pairing request; deletion failure aborts pairing. Pairing codes are forbidden on argv to avoid process-list/shell-history disclosure.

The node generates its key locally and signs the canonical pair payload to prove private-key possession.

Successful pairing creates:

```text
device_id
route_generation = 1
state = ONLINE
```

A revoked device cannot be un-revoked. Re-pairing after revoke creates a new device identity.

## Node authentication and replay protection

Every authenticated node request carries:

- `X-RMCP-Device`
- `X-RMCP-Route-Generation`
- `X-RMCP-Timestamp`
- `X-RMCP-Nonce`
- `X-RMCP-Signature`

Canonical signed string:

```text
RMCPNODE1
<device_id>
<route_generation>
<HTTP_METHOD>
<PATH>
<timestamp_ms>
<nonce>
<body_sha256>
```

Signature: Ed25519.

Rules:

- timestamp window: +/-60 s;
- nonce entropy: >=128 bits;
- nonce retained for replay rejection: 600 s;
- `UNIQUE(device_id, nonce)`;
- route generation must exactly match current device row;
- non-loopback transport requires HTTPS.

Errors include:

- `DEVICE_SIGNATURE_INVALID`
- `DEVICE_SIGNATURE_STALE`
- `DEVICE_REPLAY`
- `DEVICE_ROUTE_GENERATION_MISMATCH`

## Device state machine

Frozen states:

```text
ONLINE
   |
   | 60 s without valid signed request
   v
OFFLINE
   |
   | same identity reconnects
   v
ONLINE

ONLINE/OFFLINE
   |
   | owner revoke
   v
REVOKED
```

Heartbeat interval: 15 s.

OFFLINE does **not** mean failover.

When OFFLINE:

- no new mutating command or job is dispatched;
- request returns `DEVICE_OFFLINE`;
- queued work is not sent to another node;
- running-job metadata is preserved at last known state;
- task/project bindings remain unchanged.

REVOKED is terminal.

On revoke:

- route generation increments;
- future signed traffic is rejected;
- queued commands are cancelled;
- already leased commands become `IN_DOUBT`;
- task/worktree/job metadata is preserved;
- no project/task migrates automatically.

## Durable command routing

Central command states:

```text
QUEUED
  -> LEASED
      -> SUCCEEDED
       | FAILED
       | IN_DOUBT
  -> CANCELLED
```

Node command states:

```text
RECEIVED
  -> EXECUTING
      -> SUCCEEDED
       | FAILED
       | IN_DOUBT
```

Delivery is at-least-once to the **same device only**.

Node persistence makes execution idempotent:

- same `command_id + request_hash` -> replay stored result;
- same `command_id + different request_hash` -> `COMMAND_CONFLICT`.

Delivery lease: 45 s.

Mutation command TTL: 30 s and never later than the active task lease expiry.

A stale command is not allowed to execute after lease/task expiry.

Gateway waits at most 55 s for a synchronous routed result. A still-valid durable command beyond that returns `DEVICE_COMMAND_PENDING`; retrying the same operation resumes the existing command rather than creating another.

## Project -> device binding

Central table:

`project_device_bindings`

A project is registered on an explicit device using:

`project_register_on_device`

Protocol:

1. device must be ONLINE;
2. node `PROJECT_PROBE` resolves path under its local `MCP_NODE_ROOT`;
3. node reports GIT/NON_GIT;
4. central project + device binding is persisted;
5. node persists `project_id -> local root`;
6. operation succeeds only after both sides agree.

After the first execution device is paired, legacy `project_register` is rejected with:

`DEVICE_CONTEXT_REQUIRED`

Explicit `project_bind_device` is allowed only before unsafe state exists.

No rebind when:

- a non-terminal task exists;
- a non-terminal routed job exists;
- a task-device binding depends on old placement;
- old device is OFFLINE and has prior execution state.

Offline-device migration is **not part of V2-BD**. It would require a separate prospective migration protocol.

## Task device inheritance

Central table:

`task_device_bindings`

At task creation:

```text
project device_id
+ binding_generation
       ↓
copied into task_device_bindings
```

The task binding is immutable.

Caller cannot supply or override device ID on task operations.

Git `task_claim` now means:

```text
central lease claim
    ↓
route TASK_WORKTREE_ENSURE
to task's device
    ↓
node validates/creates task worktree
    ↓
central RUNNING
```

If the device is OFFLINE before claim, claim fails `DEVICE_OFFLINE`.

If disconnect happens during the crash window, the durable command remains tied to the same device and is reconciled; it is never sent to another node.

## Routed operations

Frozen node command types:

- `PROJECT_PROBE`
- `PROJECT_BIND`
- `TASK_WORKTREE_ENSURE`
- `TASK_LIST_DIR`
- `TASK_READ_FILE`
- `TASK_SEARCH`
- `FILE_WRITE_CAS`
- `FILE_EDIT_CAS`
- `JOB_SUBMIT`
- `JOB_GET`
- `JOB_LOGS`
- `JOB_RESULT`
- `JOB_CANCEL`
- `TASK_WORKTREE_STATUS`
- `TASK_WORKTREE_CLEANUP`

Node resolves all paths under its own `MCP_NODE_ROOT` and persisted project binding.

Task-bound jobs receive diagnostic environment variables:

- `REMOTEMCP_DEVICE_ID`
- `REMOTEMCP_PROJECT_ID`

These are evidence/debug fields, not routing authority.

## Routed durable jobs

Gateway returns central proxy IDs:

`rjob_<...>`

The node runs a local V2-A durable job and maps:

```text
proxy_job_id
   -> device_id
   -> node_job_id
```

If node goes OFFLINE while the job runs:

- proxy keeps last known job state;
- it reports device state OFFLINE;
- it is not marked FAILED or LOST merely because transport disappeared;
- no replacement job starts elsewhere.

When the same node reconnects, its local V2-A supervisor reconciles the process and reports the same proxy/node job pair.

## Public V2-BD tools

Exact signatures are frozen in `specs/v2bd_prelock.json`.

New public tools:

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

Existing V2-B task mutation/job tools become internally device-routed when the task has a device binding.

Gateway-local V2-A `job_*` remain gateway-local; remote task jobs use task-scoped routed-job proxy semantics.

## Node runtime boundary

Node is **not** a second public OAuth MCP server.

It has:

- no public inbound listener;
- no owner OAuth;
- no direct ChatGPT connection;
- only outbound authenticated node transport.

Node root:

`MCP_NODE_ROOT`

Node runtime:

`MCP_NODE_RUNTIME_DIR`

Qualification roots must be new isolated directories and must not be an existing research workspace.

## Two real execution-machine qualification

Qualification is mandatory and uses:

- `physical-machine-1`
- `physical-machine-2`

It must **not** bind ArcLLM, CQG, SIX or other research repositories.

Use isolated pilot Git repositories on both machines.

Required gates:

1. pair both physical machines as distinct `dev_*` identities;
2. distinct Ed25519 key fingerprints;
3. both ONLINE concurrently for at least 120 s;
4. one isolated pilot Git project on each device;
5. project A -> device A and project B -> device B;
6. tasks inherit the correct immutable device;
7. routed read/list/search return only the correct machine's data;
8. CAS on A mutates only A, CAS on B mutates only B;
9. routed job on each node reports its own `REMOTEMCP_DEVICE_ID`;
10. negative cross-route check: A's task never executes on B and vice versa;
11. stop node B and observe OFFLINE <=60 s while A stays ONLINE;
12. B-bound command returns `DEVICE_OFFLINE`, never fails over to A;
13. A continues working while B is offline;
14. restart B with same key and recover same `device_id`;
15. no duplicate command side effect on reconnect;
16. durable job on B survives node-agent restart and returns through same proxy/node job IDs;
17. duplicate signed nonce -> `DEVICE_REPLAY`;
18. stale timestamp -> `DEVICE_SIGNATURE_STALE`;
19. duplicate command ID/same hash -> stored-result replay;
20. duplicate command ID/different hash -> `COMMAND_CONFLICT`;
21. create a disposable extra pairing identity and revoke it to prove REVOKED terminal behavior without destroying either primary pilot device.

The run must use frozen code/config. No selective rerun may rescue a failed routing assertion. An infrastructure-only retry is allowed only if an external interruption is documented before any affected command outcome is observed.

## Observability

Device becomes the top grouping dimension for the future read-only operations view:

```text
Device
  -> Project
     -> Task
        -> Agent / Session
           -> Job
```

Pairing codes, private keys, signatures, OAuth secrets and lease tokens must never appear in that view.

## Explicitly forbidden in this prelock

- create real `devices` rows;
- generate a real pairing code;
- pair either physical machine as execution node;
- open inbound port on machine 2;
- change Cloudflare routing;
- bind a research project;
- migrate an existing task;
- implement migration 003;
- implement node transport;
- run the two-machine execution qualification;
- implement the `/ops` UI.

## Next gate

After static prelock validation PASS:

`REMOTE_MCP_V2BD_IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK`

## Prospective amendment before implementation lock

Implementation-feasibility review found two correctness gaps before any V2-BD runtime code was written.

### Multi-step routed commands per operation

A high-level operation may require more than one durable routed command. The canonical example is `project_register_on_device`:

```text
operation_id
  step 0 -> PROJECT_PROBE
  step 1 -> PROJECT_BIND
```

Therefore `device_commands.operation_id` is **not unique by itself**.

The central schema now stores:

- `operation_id`;
- `operation_step`.

A partial unique index freezes:

```text
UNIQUE(operation_id, operation_step)
WHERE operation_id IS NOT NULL
```

This preserves command creation idempotency while allowing ordered multi-command workflows.

### Node-local CAS intent journal

A routed CAS write can crash after atomic replacement but before the node persists the command result. The node therefore needs the same crash-proof evidence pattern already proven in V2-B.

Node schema now adds:

`node_cas_mutations`

with:

```text
PREPARED -> REPLACED -> COMMITTED
     \----------------> ABORTED
```

The node records expected-before hash, intended-after hash and temp path before filesystem replacement. On restart it reconciles file/temp hashes and never blindly repeats the replacement.

If the routed mutation has expired before replacement, no new replacement is permitted. If replacement already happened, the node may finalize/replay evidence only.

### Dedicated node durable-job subruntime

The execution node reuses the existing V2-A `DurableService` for process durability, isolated under:

```text
<MCP_NODE_RUNTIME_DIR>/durable/
  runtime.db
  jobs/
```

Routing/idempotency state remains separately owned by:

`<MCP_NODE_RUNTIME_DIR>/node.db`

The local V2-A operation ID for a routed job submit is deterministic:

```text
v2bd-node-job:<device_id>:<command_id>
```

so replay after node-agent crash discovers/reuses the same local durable job rather than launching a duplicate.

A node also holds an exclusive local runtime lock:

`<MCP_NODE_RUNTIME_DIR>/node.lock`

to prevent two node-agent processes from using the same runtime directory on one host.

### Routed-job observation parity

Implementation review found that remote durable jobs cannot safely use gateway-local V2-A `job_get/job_logs/job_result`, because those tools read the gateway's local job store rather than the bound execution node.

V2-BD therefore adds three task-scoped read tools:

- `task_job_get(task_id, proxy_job_id, refresh=True)`;
- `task_job_logs(task_id, proxy_job_id, stream='stdout', cursor=0, max_bytes=65536)`;
- `task_job_result(task_id, proxy_job_id)`.

Rules:

- `task_jobs` remains a cached central list and works while the device is OFFLINE;
- `task_job_get(refresh=True)` refreshes from the node only when ONLINE, otherwise returns cached state plus device state;
- `task_job_logs` routes only to the task's bound device and returns `DEVICE_OFFLINE` if that node is offline;
- `task_job_result` routes to the bound device unless a terminal result is already durably cached centrally;
- gateway-local `job_get/job_wait/job_logs/job_result/job_cancel` reject `rjob_*` IDs with `ROUTED_JOB_USE_TASK_TOOLS`.

This keeps local and remote job namespaces explicit and prevents accidental observation of the wrong job database.

### Pairing response replay without cleartext storage

`device_pair_begin(operation_id)` must be safe if the MCP response is lost after the pairing row is committed.

A runtime-local 32-byte key is therefore frozen at:

`<MCP_RUNTIME_DIR>/device-pairing.key`

The pairing row stores a random `code_nonce` plus SHA-256(clear code). The clear code is derived as:

```text
pc1_<nonce>_<base64url(
  HMAC-SHA256(
    pairing_secret,
    pairing_id|owner_account_id|requested_name|nonce
  )
)>
```

The clear pairing code is never persisted in SQLite, operation result JSON, logs or events.

A replay of the same successful `device_pair_begin` may re-derive the same code only while that exact pairing is unused and unexpired. If the pairing has already been consumed, replay returns its used status/device ID without returning the clear code.

If unused/unexpired pairing rows exist but the pairing secret is missing or unreadable, startup fails closed with `STARTUP_FATAL_PAIRING_KEY_MISSING`.

## Pairing CLI identity amendment

Implementation exposed one transport/CLI mismatch before the pair CLI was written:

- `/device/v1/pair` requires `pairing_id`;
- the secret code format `pc1_<nonce>_<mac>` intentionally does not encode `pairing_id`;
- the previously frozen CLI accepted only `--code-file`, so a node could not construct the required pair request.

Prospective correction:

```text
python -m remotemcp.node pair
  --url <URL>
  --pairing-id <PAIRING_ID>
  --code-file <FILE>
  --name <NAME>
  --root <ROOT>
  --runtime-dir <RUNTIME>
```

`pairing_id` is non-secret routing metadata. The clear pairing code remains secret, remains file-only, and is still deleted before the network request. No pairing-code format, HMAC derivation, one-time semantics, or HTTP pair payload semantics are weakened.
