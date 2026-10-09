# SIX / IRIS — Khóa nâng cấp node an toàn và đối soát lệnh chính xác

Trạng thái tài liệu: `EXECUTION_HOLD` (2026-10-09). Hạ tầng, **không phải** kết luận khoa học.

## Khóa danh tính trước mọi thay đổi

- `machine-1` / `<EXISTING_DEVICE_ID>`
- Fingerprint SHA-256: `<EXISTING_FINGERPRINT_SHA256>`
- Route generation: `1`
- Node root: `<CANONICAL_RESEARCH_ROOT>`
- Runtime: `<EXISTING_NODE_RUNTIME_DIR>`
- Gateway at time of audit: `<CURRENT_GATEWAY_RELEASE_COMMIT>`
- Node at time of audit: `<LEGACY_NODE_RELEASE_COMMIT>`
- PID `9152` and parent `24528`: protected CQG-RU executor; created 2026-10-06 07:38:15 local. Operator repeatedly confirmed alive; no termination/restart/resubmit.
- Watchdog release pinned to old node `80fe818`; MUST coordinate its source before a cutover. An unattended restart of the node alone can cause rollback to old source.

## SIX exact pending proxy inventory (immutable IDs)

| Existing task | Existing proxy | Cached state |
|---|---|---|
| `<FROZEN_TASK_ID>` | `<FROZEN_PROXY_JOB_ID>` | QUEUED; node_job_id NULL |
| `<FROZEN_TASK_ID>` | `<FROZEN_PROXY_JOB_ID>` | QUEUED; node_job_id NULL |

`<FROZEN_PROJECT_ID>` is the canonical SIX Git project and `<FROZEN_PROJECT_ID>` is its canonical non-Git workspace project. Both are already registered; **never re-register or widen root to fix routing**.

IRIS existing non-Git project `<FROZEN_PROJECT_ID>` is ACTIVE, task `<FROZEN_TASK_ID>` BLOCKED without a routed job. IRIS P2 historical RAM precheck FAIL (8.946 GiB free versus 12.000 GiB frozen threshold); rerun fresh READ-ONLY resource precheck only after dispatch works. No A/B model download/inference authorized.

Other infrastructure probe `<FROZEN_TASK_ID>` / `<FROZEN_PROXY_JOB_ID>` also remains QUEUED and unmapped, and MUST NOT be resubmitted for testing.

## Required *read-only* exact dispatch reconciliation before cutover

The production gateway toolset does not yet expose `task_dispatch_diagnostic` from PR #59, so a gateway-only frozen command snapshot cannot currently be obtained via this connector. Existing `task_job_get(refresh=false)` is a cached proxy record, not a command-delivery receipt. Missing proof is `UNRESOLVED`, **not** `delivery_attempt=0`.

For each frozen SIX proxy:
1. Resolve the exact `command_id`, `operation_id`, `operation_step`, `command_type` (= `JOB_SUBMIT`), `request_hash`, device/route/binding generation, original task/project, expiration and `delivery_attempt`. Require correct original proxy in immutable command payload. Do this using read-only gateway snapshot; do not invoke poll.
2. Match command against node-side durable journal by exact ID, generation, request hash and task; never call `JOB_SUBMIT` for recovery or resurrect a cancelled delivered command.
3. Classify as `NOT_DELIVERED`, `RECEIVED`, `EXECUTING`, `TERMINAL_VERIFIED` or `UNRESOLVED` based on evidence, not guesswork. Any `RECEIVED/EXECUTING/UNRESOLVED` remains HOLD; terminal jobs require exact proxy/node mapping and terminal evidence.
4. Preserve proxy rows, operation ledger, checkpoint, source git hashes and worktree files. No `task_claim`, `task_job_submit`, `task_job_cancel`, `cleanup_pending` clearance, SQLite writes, `git clean` or `git reset --hard` while unproven.
5. PR #59 adds a read-only gateway diagnostic, PR #60 adds node journal exact mapping and evidence manifest, but neither is deployed. PASS in hosted CI is NOT proof of live command reconciliation.

## Node upgrade preflight and hard HOLD conditions

`scripts/Collect-SIX-IRIS-SafeNodeUpgradeOS.ps1` outputs a local read-only OS snapshot (same runtime identity, process topology, lock-file *presence only*); it does not read private keys or alter node. `scripts/six_iris_safe_node_upgrade_gate.py` assesses a frozen composite snapshot containing the local OS data, a recent signed gateway state, pinned expected identities/process creation times, staged release exact hash, and per-proxy authenticated command evidence.

The following block cutover:
- Missing/stale gateway signed heartbeat, wrong root/runtime/identity/fingerprint/generation.
- Any unverified runtime node root PID/creation time, non-unique logical node root, node PIDs in the protected PID ancestry, or protected PID missing/changed creation time.
- Watchdog missing, PID changed or pinned to a *different release* than the staged target.
- Target source/venv not staged and independently hash-verified.
- Any fresh active managed job or unresolved node job capacity.
- Any unknown command ID/delivery attempt, unreconciled node journal or missing exact proxy mapping.
- No independently documented operator approval to perform cutover and an immediate rollback route that cannot touch existing scientific child processes.

`EVIDENCE_PREFLIGHT_PASS` **never** authorizes a cutover; the assessor always outputs `cutover_authorized=false` and `science_rerun_authorized=false`. The tests are purely synthetic fixtures; no production tests were run.

## If all prerequisites have been independently certified

Prepare a release and virtual environment under a non-OS drive without editing current runtime. Freeze hashes and prepare a watchdog transition plan; only then an operator-approved cutover may proceed using the existing device identity and runtime directory. Keep node pause/cutover distinct from scientific work. Before and after: compare protected PID and process creation time, check science output unchanged, verify exactly one logical node root and watchdog source, verify signed heartbeat and route generation, and reconcile original command IDs **before** any queued job can execute. On failure, fail closed and use a state-preserving rollback plan; no scientific reruns.

## Current gate

- A read-only diagnostics PR #59: hosted QA PASS; live gateway exposure not yet available.
- B read-only journal evidence PR #60: hosted QA PASS; node production is older and lacks API.
- C safe-upgrade preflight PR #61: staged code and hosted zero-science QA; not deployed.
- SIX live execution: **BLOCKED**.
- IRIS live execution: **BLOCKED**, historical resource gate still unresolved.
- Doctor V2: **DEFERRED** until SIX and IRIS have a stable execution path.

Do not append to scientific `lineage.md` for infrastructure failures.
