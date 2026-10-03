# RemoteMCP Control-Plane Read Path SQLite Write-Contention Fix — Executable QA and Deployment Lock

Date: 2026-10-03

Gate:

`REMOTEMCP_CONTROL_PLANE_READ_PATH_SQLITE_WRITE_CONTENTION_FIX_EXECUTABLE_QA_AND_DEPLOYMENT_LOCK`

Verdict:

`PASS — DEPLOYMENT LOCKED / MAINTENANCE DEPLOYMENT AUTHORIZED`

## Exact QA identity

Git commit:

`203b77cb527d8f3809747bd2f54c95dcd9f3cf76`

Frozen Git blob identities:

- `remotemcp/routing/devices.py`: `19fc04e2841822b4ce524c7c9e29ac7c747d448c`
- `remotemcp/routing/service.py`: `fcabb368b082c56f7543772cc679e4f4904f818e`
- `tests/v2bd/test_device_read_path_contention.py`: `d85e9e688b32e27b8dd36d99c1edff06d57b5e93`
- `.github/workflows/readpath-sqlite-qa.yml`: `dd77cd2d1eef74fa4e9305583e49191a72c26fee`

GitHub Actions run:

- workflow: `RemoteMCP Read-Path SQLite QA`
- run id: `37111994312`
- runner: `windows-latest`
- Python: `3.13`
- conclusion: `success`

## Executable QA matrix

Every required step completed with conclusion `success`:

1. checkout exact commit;
2. install QA dependencies;
3. freeze source identities;
4. targeted device read-path contention QA;
5. full V2-BD regression;
6. full V2-B regression;
7. full V2-A regression;
8. frozen V2-0 denominator.

The targeted QA includes:

- `tests/v2bd/test_device_states.py`
- `tests/v2bd/test_device_read_path_contention.py`

The regression runners are the repository-owned frozen entry points:

- `scripts/run_v2bd_tests.py`
- `scripts/run_v2b_tests.py`
- `scripts/run_v2a_tests.py`
- `scripts/run_v2_0_tests.py`

## Deployment lock

The exact source identity above is the only version authorized by this gate for the read-path contention maintenance deployment.

Any source change to the routing/device implementation after this commit invalidates this deployment lock and requires executable QA again.

This gate does **not** itself restart or deploy the production gateway.

Maintenance deployment is now allowed, subject to the operational rule:

- preserve persistent OAuth/runtime state;
- do not re-pair or revoke devices;
- do not relaunch scientific jobs;
- do not create a second scientific execution;
- verify gateway/local/public health after deployment;
- verify `device_list` and concurrent `device_status` responsiveness;
- preserve existing routed-job identities.

## CQG operational successor

Because executable QA passed, the following operational gate is opened:

`REMOTEMCP_CQG_MANAGED_PROJECT_REGISTRATION_PATH_BINDING_DIAGNOSTIC_AND_FIX`

That gate must:

- use the CQG repository only;
- not substitute an ArcLLM project/task;
- identify the exact node-root/path relationship or structured project-probe failure;
- register/bind the correct CQG project if the path is permitted;
- create a CQG-specific managed task;
- use only structural/dry execution for verification;
- not open scientific seeds or scientific inference.
