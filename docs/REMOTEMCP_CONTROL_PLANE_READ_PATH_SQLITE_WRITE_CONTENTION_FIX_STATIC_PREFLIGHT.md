# RemoteMCP Control-Plane Read Path SQLite Write-Contention Fix — Static Preflight

Date: 2026-10-03

Gate:

`REMOTEMCP_CONTROL_PLANE_READ_PATH_SQLITE_WRITE_CONTENTION_FIX_STATIC_PREFLIGHT`

Verdict:

`PASS — SOURCE-STAGED / NOT DEPLOYED`

## Incident mechanism

The V2-BD device read path was not actually read-only.

Before this fix:

```text
device_list
  -> status(device)
      -> sweep_offline()
          -> BEGIN IMMEDIATE
```

and:

```text
require_online
  -> sweep_offline()
      -> BEGIN IMMEDIATE
```

At the same time, the routing background loop already persisted offline transitions every five seconds.

This meant request-time `device_list`, `device_status`, routing metadata reads, node online checks, and project/task routing guards could introduce SQLite writers while other control-plane operations were active.

The durable database uses WAL with a 5-second SQLite busy timeout. Under concurrent agents, unnecessary `BEGIN IMMEDIATE` calls on read/status paths could therefore serialize or stall MCP CallTool processing.

## Locked fix

`DeviceRepository.effective_state(row)` now computes request-time state without mutating SQLite:

```text
stored REVOKED/OFFLINE
  -> unchanged

stored ONLINE
  + last_seen_at_ms older than offline threshold
  -> effective OFFLINE

stored ONLINE
  + fresh heartbeat
  -> effective ONLINE
```

Request-time behavior:

- `status()` is pure-read;
- `require_online()` is pure-read and remains fail-closed;
- stale heartbeat is rejected as `DEVICE_OFFLINE` before the persistence sweep runs;
- `device_list` and `device_status` no longer trigger offline-state writes.

Persistence behavior:

- the routing background loop remains the sole periodic offline-state writer;
- `sweep_offline()` still persists ONLINE -> OFFLINE transitions and audit events;
- a transient `DB_BUSY` during the background sweep no longer kills the routing loop; the next cycle retries;
- non-`DB_BUSY` sweep errors still fail loud.

## Regression coverage added

`tests/v2bd/test_device_read_path_contention.py` covers:

1. `status`, `device_list`, and `require_online` do not invoke request-time persistence sweep;
2. stale heartbeat returns effective `OFFLINE` without mutating durable state;
3. `require_online` fails closed for effective `OFFLINE`;
4. the background sweep later persists `OFFLINE`;
5. `device_status` and `device_list` remain responsive while another SQLite connection holds `BEGIN IMMEDIATE`;
6. transient `DB_BUSY` does not terminate the background offline writer loop.

## Static source invariants

Current-main inspection confirmed:

- `effective_state()` exists;
- `status()` contains no `sweep_offline()`;
- `require_online()` contains no `sweep_offline()`;
- both use effective-state freshness;
- the background routing loop still calls `sweep_offline()`;
- only `DB_BUSY` is suppressed in the background loop;
- writer-lock, stale-state, no-request-sweep, and DB_BUSY-loop regressions are present.

## Isolated SQLite WAL contention harness

Because the external full-repository QA runners were unavailable, an isolated harness was executed outside the operator machines using the same SQLite settings and the new DeviceRepository read/freshness logic.

Observed:

```text
writer lock: BEGIN IMMEDIATE held by another connection
device status/list read elapsed: ~1.32 ms
effective stale state: OFFLINE
stored state before sweep: ONLINE
stored state after explicit sweep: OFFLINE
```

Harness verdict:

`PASS`

The temporary harness file was deleted immediately after execution.

## Full pytest boundary

Two independent external QA runners were attempted and both were unavailable at the harness transport layer with an MCP SSE probe HTTP 404. The local container could not clone GitHub because DNS is disabled.

Therefore this gate does **not** claim full-repository pytest PASS.

This is not a test failure.

## Deployment lock

This change is **not deployed** by this gate.

Do not restart the production gateway or nodes as part of this static preflight.

Do not relaunch or alter scientific executions.

Before deployment:

1. run targeted V2-BD tests including `test_device_states.py` and `test_device_read_path_contention.py`;
2. run the existing V2-BD regression suite;
3. run relevant V2-B/V2-A/V2-0 regression;
4. freeze the exact source commit;
5. deploy only in an explicit operational window;
6. after deployment, verify public OAuth metadata, unauthenticated `/mcp` response, `device_list`, concurrent `device_status`, and existing routed jobs without resubmission.

## Scientific boundary

This is an infrastructure/control-plane fix.

It must not be recorded as a scientific PASS/FAIL in any research `LINEAGE.md`.
