# RemoteMCP gateway updater fail-safe hardening

Date: 2026-10-03

Gate:

REMOTEMCP_CONTROL_PLANE_READ_PATH_SQLITE_WRITE_CONTENTION_FIX_MAINTENANCE_DEPLOYMENT_RECOVERY_HARDENING

Status:

PASS — FAIL-SAFE HARDENING LOCKED / NOT DEPLOYED

## Trigger

The first maintenance attempt to deploy the already QA-locked read-path fix commit failed during candidate gateway startup.

The updater restored source_dir to the previous release, but that old release predated the recovery scripts, so the updater could not automatically restart the old gateway. Manual use of the newer state-preserving starter restored the old release successfully.

Production evidence after manual recovery:

- old gateway source restored;
- local OAuth metadata HTTP 200;
- connector device_list recovered;
- the intended execution node remained ONLINE with unchanged identity/fingerprint/route generation;
- an existing routed job remained readable and SUCCEEDED;
- no scientific job was resubmitted.

The failed candidate stderr was lost because the subsequent manual old-release start reused the conventional gateway log path. This hardening prevents both failure modes.

## Hardening

Update-RemoteMCP-Gateway.ps1 now:

1. uses the configured production Python to start the exact candidate release against fresh temporary workspace/state/runtime;
2. binds that probe to a dynamically selected loopback port;
3. requires OAuth metadata HTTP 200 before any live config mutation;
4. supports -PreflightOnly, which exits before config backup/switch;
5. preserves failed live cutover stdout/stderr under timestamped diagnostic names before rollback;
6. restores the previous config;
7. uses the candidate release's validated Start-RemoteMCP-Gateway.ps1 -Restart after config restoration, so rollback can restart an older source even if that old source predates recovery scripts;
8. never pairs/revokes devices and never restarts execution nodes.

## Independent source diagnosis

A separate diagnostic CI reproduction from the exact read-path QA commit showed:

- fresh-state gateway startup: PASS;
- old-release synthetic startup: PASS;
- synthetic old-release -> QA-commit startup on the same state/runtime: PASS.

Therefore the first production failure is not currently reproduced as a generic source-startup or synthetic state-compatibility defect. The strengthened same-machine isolated probe is required before a retry.

## Retry boundary

Do not retry the maintenance cutover until:

- PowerShell parse QA passes;
- -PreflightOnly executes the isolated startup probe successfully on Windows CI;
- gateway recovery/static tests pass;
- public-repository hygiene passes;
- the exact hardened updater blob is frozen.

The deployment target remains the previously QA-locked read-path commit. Hardening the updater does not change the target gateway source identity.


## Windows executable QA lock

GitHub Actions run: 37131354393

Observed:

- PowerShell parse preflight: PASS
- isolated exact-release startup probe through updater -PreflightOnly: REMOTEMCP_GATEWAY_RELEASE_PROBE=PASS
- preflight-only early exit before live mutation: REMOTEMCP_GATEWAY_UPDATE_PREFLIGHT_ONLY=PASS
- targeted recovery/public-hygiene tests: 8 passed

Frozen blobs:

- Update-RemoteMCP-Gateway.ps1: 9df4c087da6611e55f231fd8149bbf24de6fe0c1
- tests/test_gateway_recovery_scripts.py: 93bcb38d45c6064375addae7e6748371596532f9
- .github/workflows/gateway-updater-failsafe-qa.yml: 3b74724af5b356aec2252dd34cff28deeb703823

Any change to the hardened updater, its targeted tests, or its QA workflow requires executable QA again before maintenance retry.

No production gateway, execution node, device identity, task, or scientific job was mutated by this hardening QA.

## Schema-v5 production-state compatibility recovery

During the protected rolling-upgrade preflight on 2026-10-08, production `runtime.db` was observed with a contiguous migration ledger `[1,2,3,4,5]`. The earlier updater probe was still hard-coded to accept only pre-v4/v4 ledgers and therefore failed closed before any live mutation.

The updater now treats `[1,2,3]`, `[1,2,3,4]`, and `[1,2,3,4,5]` as valid starting prefixes for the current release, migrates only a backup to target schema v5, verifies existing ledger entries are byte-identity preserving, validates the v4 admission schema and v5 observability/binding-lifecycle schema, and confirms all pre-existing row data is unchanged across the probe. An already-v5 production backup must remain exactly v5 and must not be rewritten.

Windows executable QA includes both a pre-v4 synthetic runtime and an already-v5 synthetic runtime. `-PreflightOnly` must leave the live synthetic database hash unchanged in both cases.
