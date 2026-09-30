# V2-BD Implementation Static Preflight and Execution Lock

Status: **FROZEN / PASS**
Gate: `REMOTE_MCP_V2BD_IMPLEMENTATION_STATIC_PREFLIGHT_AND_EXECUTION_LOCK`
Date: 2026-09-30

This gate freezes the exact V2-BD implementation surface. It does **not** create routing/node runtime code, apply migration 003, pair a device, or change production.

Bound amended prelock commit:

`858c9155c329726f7b8e67683387212f6ea16c17`

Machine source of truth:

`specs/v2bd_implementation_lock.json`

## Exact gateway package

```text
remotemcp/routing/
  __init__.py
  config.py
  models.py
  crypto.py
  devices.py
  pairing.py
  auth.py
  commands.py
  bindings.py
  routed_jobs.py
  service.py
  http.py
  migrations/
    003_v2bd.sql
```

The package owns device identity, pairing, signed-node authentication, central command routing, device bindings and routed-job proxy state.

## Exact node package

```text
remotemcp/node/
  __init__.py
  __main__.py
  cli.py
  config.py
  identity.py
  runtime_lock.py
  db.py
  models.py
  signing.py
  client.py
  command_journal.py
  executor.py
  projects.py
  worktrees.py
  cas.py
  jobs.py
  service.py
  migrations/
    001_node.sql
```

The node has no OAuth server, no public inbound listener and no ChatGPT endpoint. It only initiates outbound HTTPS traffic.

## Migration bootstrap

Central startup order is frozen:

```text
V2-A DurableService      -> schema 001
V2-B MultiAgentService   -> schema 002
V2-BD RoutingService     -> schema 003
```

`003_v2bd.sql` must be byte-identical to `specs/v2bd_schema_v3.sql`.

Node `001_node.sql` must be byte-identical to `specs/v2bd_node_schema_v1.sql`. Node DB stores and verifies its exact schema SHA-256.

Migration 003 is **not applied to production in this gate**.

## Device identity

Node private key is Ed25519 PKCS8 PEM at:

`<MCP_NODE_RUNTIME_DIR>/device-ed25519.pem`

Paired identity is atomically persisted in `device.json`. Startup derives the public key/fingerprint from the private key and compares it against `device.json` and `node_meta`. Any mismatch fails closed; a paired identity never regenerates its key.

A local exclusive `node.lock` prevents two node-agent processes from sharing one runtime directory.

## Pairing

`device_pair_begin` uses `<MCP_RUNTIME_DIR>/device-pairing.key` to derive a replayable one-time code without persisting cleartext.

Node pairing uses:

```text
python -m remotemcp.node pair
  --url https://remote.threadon.xyz
  --code-file <PAIRING_CODE_FILE>
  --name <DEVICE_NAME>
  --root <MCP_NODE_ROOT>
  --runtime-dir <MCP_NODE_RUNTIME_DIR>
```

The code file is deleted before any network request. Passing pairing secrets on argv is forbidden.

The unauthenticated HTTP pair endpoint is authorized by the one-time code plus Ed25519 proof-of-possession over HTTPS.

## Signed node requests

All heartbeat/poll/result traffic is signed as:

```text
RMCPNODE1
device_id
route_generation
HTTP_METHOD
PATH
timestamp_ms
nonce
body_sha256
```

Verification consumes the nonce durably after signature/timestamp/generation validation. A duplicate nonce is rejected even if business payload parsing later fails.

## Command queue

Mutation command uniqueness is:

```text
(operation_id, operation_step)
```

not operation ID alone.

`project_register_on_device` uses:

```text
step 0 -> PROJECT_PROBE
step 1 -> PROJECT_BIND
```

Poll leases exactly one oldest eligible command for the requesting device/generation. A response-loss redelivery can occur only to the same device, and node `command_id + request_hash` journaling prevents duplicate side effects.

## CAS

Remote CAS never uses gateway-local filesystem mutation.

The node persists `node_cas_mutations`:

```text
PREPARED -> REPLACED -> COMMITTED
     \----------------> ABORTED
```

A crash after replace is resolved by evidence/hash comparison, never by blindly writing a second time.

## Routed jobs

A remote job has a central:

`rjob_...`

and a node-local V2-A durable job.

Node submit operation ID is deterministic:

`v2bd-node-job:<device_id>:<command_id>`

so node-agent restart cannot launch a duplicate payload.

Task-scoped observation is mandatory:

- `task_job_get`
- `task_job_logs`
- `task_job_result`
- existing `task_job_cancel`

Gateway-local V2-A job tools reject `rjob_*` with `ROUTED_JOB_USE_TASK_TOOLS`.

## Exact CLI

Frozen commands:

```text
python -m remotemcp.node pair ...
python -m remotemcp.node run --runtime-dir <RUNTIME>
python -m remotemcp.node status --runtime-dir <RUNTIME>
python -m remotemcp.node doctor --url <URL> --root <ROOT> --runtime-dir <RUNTIME>
```

`run` loads paired origin/root from verified identity metadata; they cannot be overridden after pairing.

## Server integration

The existing FastMCP process remains the only public server.

`register_device_routes(mcp, routing_service)` attaches the four `/device/v1/*` custom routes.

Affected existing V2-B tools route through the V2-BD facade only for device-bound projects/tasks. Local V2-B behavior remains unchanged when no device binding exists.

## Failure injection

The machine lock freezes the full failure matrix, including pairing-response loss, pair race, signature/replay failures, command delivery crash windows, project two-step binding crash windows, immutable task placement, node CAS crash windows, routed-job submit/restart windows, OFFLINE/revoke behavior, cross-route negatives and `rjob_*` namespace guards.

## Isolated two-device pilot lock

Final real-device qualification is fixed to the already proven login identities:

- `physical-machine-1` / expected host label `DESKTOP-4PSD0G2`;
- `physical-machine-2` / expected host label `DESKTOP-VKIC2RU`.

Each machine uses only:

`%USERPROFILE%\RemoteMCP-V2BD-Pilot\...`

Fresh pilot Git repositories contain distinct marker files. Existing research repositories—including ArcLLM, CQG, SIX/CLDP, MindForge, cot-from-zero and RemoteMCP-src—are forbidden as pilot bindings.

The physical two-machine run cannot open until unit/failure/loopback qualification and all V2-B/V2-A/V2-0 regressions pass.

## Production boundary

Production `127.0.0.1:8099` must remain untouched during V2-BD implementation and local/loopback qualification. No Cloudflare route change and no real execution-device pairing is authorized by this lock.

## Next gate

After static PASS:

`REMOTE_MCP_V2BD_ROUTING_IMPLEMENTATION_AND_ZERO_SCIENCE_QUALIFICATION`
