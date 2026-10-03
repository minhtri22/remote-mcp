# RemoteMCP control-plane SQLite read-path contention fix — Maintenance deployment plan

Date: 2026-10-03

Gate prepared:

`REMOTEMCP_CONTROL_PLANE_READ_PATH_SQLITE_WRITE_CONTENTION_FIX_MAINTENANCE_DEPLOYMENT`

Status:

`READY FOR EXPLICIT MAINTENANCE AUTHORIZATION — NOT DEPLOYED`

## Deployment-lock revalidation

The executable QA lock remains intact on current main. The following frozen blobs still match the previously authorized identities:

- `remotemcp/routing/devices.py`: `19fc04e2841822b4ce524c7c9e29ac7c747d448c`
- `remotemcp/routing/service.py`: `fcabb368b082c56f7543772cc679e4f4904f818e`
- `tests/v2bd/test_device_read_path_contention.py`: `d85e9e688b32e27b8dd36d99c1edff06d57b5e93`
- `.github/workflows/readpath-sqlite-qa.yml`: `dd77cd2d1eef74fa4e9305583e49191a72c26fee`

Executable-QA commit:

`203b77cb527d8f3809747bd2f54c95dcd9f3cf76`

Executable-QA workflow run:

`37111994312 — success`

The gateway update/restart/test scripts are also byte-identical between the QA commit and current main.

A compare from the QA commit to the pre-plan current main showed only documentation, README, and ChatGPT plugin-builder/test changes; no gateway routing/runtime source changed.

## Frozen deployment candidate

To maximize deployment-lock fidelity, the maintenance action should deploy the exact executable-QA commit:

`203b77cb527d8f3809747bd2f54c95dcd9f3cf76`

Do not substitute a moving branch name during the maintenance action.

## Pre-deployment evidence

Before mutation:

1. obtain explicit maintenance authorization;
2. capture local/public OAuth metadata health;
3. capture live `device_list`;
4. capture live `device_status` for currently online execution nodes;
5. record a small set of existing routed task/job identities using read-only `task_job_get`;
6. verify no pairing/revocation operation is planned;
7. verify the four frozen blob identities above;
8. verify the exact QA commit is available locally;
9. retain the current gateway source/config as the rollback target.

## Maintenance action

Use the repository-owned state-preserving gateway updater pinned to the exact QA commit. The updater:

- creates a new immutable release snapshot;
- reuses the configured Python environment;
- syntax/import preflights before touching the live process;
- backs up gateway configuration;
- switches only the gateway source directory;
- restarts the gateway;
- rolls configuration back automatically if its local post-upgrade checks fail;
- preserves durable OAuth/runtime/device state;
- does not restart execution nodes.

No scientific job may be resubmitted or relaunched.

## Post-deployment verification

Require all of the following:

1. local OAuth metadata returns quickly with HTTP 200;
2. public OAuth metadata returns quickly with HTTP 200;
3. unauthenticated `/mcp` returns promptly with the expected authentication response;
4. live `device_list` succeeds;
5. several concurrent/read-only `device_status` calls succeed without the former request-time SQLite writer stall;
6. online device IDs, fingerprints, and route generations are unchanged;
7. previously recorded `task_job_get` identities are still readable and unchanged;
8. no task/job resubmission occurred.

Only after those checks may the maintenance gate be adjudicated PASS.

## Rollback

If connector-level verification fails after the updater's own local health check, restore the retained pre-upgrade gateway configuration/source snapshot and restart the gateway only. Do not restart execution nodes unless an independent node-process failure is demonstrated.

## Scientific boundary

This is infrastructure/control-plane maintenance. Do not append it to scientific `LINEAGE.md`.
