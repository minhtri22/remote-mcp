# REMOTEMCP_MANAGED_EXECUTION_ENVIRONMENT_PROPAGATION_STATIC_QUALIFICATION

Status: **SOURCE PATCH / NOT DEPLOYED**
Date: 2026-10-07

## Problem

RemoteMCP durable workers intentionally construct a reduced child environment through
`safe_child_env()`. This prevents arbitrary parent-process secrets from leaking into
managed jobs, but it also meant model/provider credentials such as
`NVIDIA_API_KEY` were absent from a managed task even when the variable existed in
Windows Environment Variables.

A second Windows-specific failure mode exists for long-lived node/service processes:
adding a persistent environment variable after the service started does not mutate
that already-running process environment.

## Contract

Managed execution now uses an explicit pass-through allowlist.

- `NVIDIA_API_KEY` is allowlisted by default.
- Additional names may be declared with
  `REMOTEMCP_MANAGED_ENV_ALLOWLIST` as a comma-separated list.
- Arbitrary parent environment variables remain excluded.
- Credential values are copied only into the spawned process environment. They are
  not added to job command JSON, provenance, logs, SQLite rows, or scientific
  evidence by this mechanism.
- On Windows, if an allowlisted value is absent from the already-running service
  process, RemoteMCP performs a read-only lookup in the persistent User environment
  first and Machine environment second. This closes the stale-parent-process gap
  without mutating Windows environment state.
- Existing PATH/SystemRoot/TEMP and other runtime-safe keys keep their prior behavior.

## Process chain

```text
Windows persistent environment / node process environment
        |
        | explicit allowlist only
        v
RemoteMCP durable worker
        |
        | explicit allowlist only
        v
managed task subprocess
```

The same `safe_child_env()` boundary is used for both worker launch and payload
launch, so the allowlist is preserved across both process boundaries.

## Safety boundary

This change does **not** inherit the complete parent environment and does not permit a
job payload to request arbitrary secret names. Expanding the allowlist is an operator
configuration action, not task-controlled input.

This qualification is source-only. It does not authorize a live RemoteMCP upgrade,
node restart, Zero-C migration, long-run cancellation, process termination, re-pair,
or device-identity change.

## Required QA

The patch must pass on Windows and Ubuntu:

1. default `NVIDIA_API_KEY` pass-through;
2. arbitrary-secret non-leakage;
3. configured additional allowlist pass-through;
4. Windows persistent-environment fallback;
5. end-to-end durable worker -> managed subprocess propagation;
6. full V2-A regression;
7. full V2-B regression;
8. full V2-BD regression.

Only after QA PASS may the patch be merged into the qualified release source. Live
deployment remains separately gated by the active long-run/maintenance-window
contract.
