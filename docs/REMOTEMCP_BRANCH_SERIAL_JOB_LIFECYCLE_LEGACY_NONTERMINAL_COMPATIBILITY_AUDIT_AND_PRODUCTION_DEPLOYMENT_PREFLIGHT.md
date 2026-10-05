# REMOTEMCP branch-serial legacy nonterminal compatibility audit and production deployment preflight

Date: 2026-10-04

Gate:

`REMOTEMCP_BRANCH_SERIAL_JOB_LIFECYCLE_LEGACY_NONTERMINAL_COMPATIBILITY_AUDIT_AND_PRODUCTION_DEPLOYMENT_PREFLIGHT`

Status:

`IMPLEMENTATION PASS — PRODUCTION BACKUP RUN STILL REQUIRED — CUTOVER NOT AUTHORIZED`

## Bound implementation

Branch-serial admission implementation merge:

`492407e39cefa23ee1af293813effd3802b69376`

Schema target:

`v4 task_job_admissions`

## Live read-only evidence before implementation

The production control plane contains pre-v4 non-terminal routed-job shadows.

Known read-only examples include multiple task lanes with a cached `QUEUED` routed row, no central `node_job_id`, and later terminal jobs in the same task history.

The device-wide `active_routed_jobs` counters are therefore not accepted as counts of live OS processes or live durable jobs.

No legacy row may be bulk-cancelled, deleted or manually terminalized by this gate.

## Full production inventory utility

`scripts/audit_legacy_routed_jobs.py` opens a consistent SQLite backup in URI `mode=ro` with `PRAGMA query_only=ON`.

It inventories every central `routed_jobs` row whose state is not one of:

- SUCCEEDED
- FAILED
- CANCELLED
- LOST

For every non-terminal row the report binds:

- proxy/operation/task/project/device identity;
- central state;
- node-job mapping presence;
- current cached device state;
- matching JOB_SUBMIT command state and node-job mapping if present;
- existence/count of later jobs and later terminal successors in the same task lane;
- a compatibility classification;
- whether the row blocks v4 successor admission.

The report is persisted under the local RemoteMCP maintenance directory and is not committed to the public repository.

## Frozen compatibility classifications

- `NODE_BOUND_REFRESH_REQUIRED`
- `COMMAND_RESULT_CAN_REPAIR_MAPPING`
- `TERMINAL_SUBMIT_COMMAND_WITHOUT_JOB_MAPPING`
- `ONLINE_PROXY_REFRESH_REQUIRED`
- `UNRESOLVED_OFFLINE_OR_MISSING_MAPPING`

These are audit classifications only. They do not mutate central or node state.

## Schema-v4 production-state backup probe

`Update-RemoteMCP-Gateway.ps1 -PreflightOnly` now must:

1. create a consistent SQLite backup through the SQLite backup API;
2. run the full legacy routed-job inventory on that backup;
3. record the exact backup inventory report before migration;
4. require the pre-migration ledger to be exactly v1-v3 or an already-idempotent v1-v4 ledger;
5. fingerprint all legacy execution/routing tables;
6. run `Database.bootstrap(target_version=4)` against the backup only;
7. if v4 is new, require it to append exactly once with the exact candidate migration checksum;
8. require `task_job_admissions` columns and the active-lane unique index;
9. require a newly-created v4 ledger to contain zero admission rows — no historical backfill is allowed;
10. require every fingerprinted legacy table to remain unchanged;
11. require SQLite `integrity_check=ok` before and after;
12. leave the live production runtime DB untouched.

The existing release startup probe remains required before this production-state probe.

## Deployment boundary

PASS of this compatibility gate does **not** itself perform the gateway cutover.

Production deployment remains a later explicit maintenance action after:

- executable QA PASS;
- full production backup inventory captured;
- schema-v4 backup probe PASS on the production host;
- unresolved legacy rows classified;
- safe rollout boundary adjudicated.

No execution node restart belongs to this gate.

## Scientific boundary

This is infrastructure/control-plane maintenance and must not be appended to scientific lineage.


## Executable implementation QA lock

GitHub Actions run:

`37167076194`

Observed on Windows:

- PowerShell parse preflight: PASS
- isolated exact-release startup probe: `REMOTEMCP_GATEWAY_RELEASE_PROBE=PASS`
- synthetic production-v3 SQLite backup inventory: `REMOTEMCP_LEGACY_ROUTED_JOB_INVENTORY=PASS`
- schema-v4 migration on backup only: `REMOTEMCP_SCHEMA_V4_BACKUP_PROBE=PASS`
- migration compatibility marker: `REMOTEMCP_PRODUCTION_RUNTIME_MIGRATION_COMPAT=PASS`
- production-state backup probe marker: `REMOTEMCP_GATEWAY_PRODUCTION_STATE_PROBE=PASS`
- preflight-only no-cutover marker: `REMOTEMCP_GATEWAY_UPDATE_PREFLIGHT_ONLY=PASS`
- live synthetic runtime.db SHA256 before/after preflight: unchanged
- targeted recovery/inventory/public-hygiene tests: `10 passed`

The immediately preceding QA run executed the full updater probe successfully but failed one static source-string assertion because the test expected `Database(runtime_dir).bootstrap(...)` while the implementation used an equivalent `db.bootstrap(...)` call. Only that static assertion was corrected; runtime probe logic was unchanged.

Frozen implementation blobs:

- `Update-RemoteMCP-Gateway.ps1`: `064af81999ffa0e354e01dbbf153f5f4de0b65a7`
- `scripts/audit_legacy_routed_jobs.py`: `469af63390bbf313dda3279cd2f17c1ba671c5df`
- `tests/test_gateway_recovery_scripts.py`: `ef47d91dcee93e16c8c64a02edc093615873aef5`
- `tests/test_legacy_routed_job_inventory.py`: `309884e2a6b6a568a7af6fd04673c33ee9efc316`
- `.github/workflows/gateway-updater-failsafe-qa.yml`: `a112a9793d3493cb059515453e3b8fb106534d95`

## Blocker implementation adjudication

`BLOCKED_ON_FULL_PRODUCTION_LEGACY_ROUTED_JOB_INVENTORY_AND_SCHEMA_V4_BACKUP_PROBE_IMPLEMENTATION = PASS`

The blocker is removed at the source/QA level.

The parent compatibility gate remains not-yet-PASS until this exact preflight is executed against a consistent backup of the actual production runtime DB and the resulting full legacy inventory is adjudicated.

No production database or gateway process was modified by this implementation QA.
