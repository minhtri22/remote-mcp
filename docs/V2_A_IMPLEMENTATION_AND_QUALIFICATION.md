# V2-A Durability Implementation and Zero-Science Qualification

Status: **PASS / FORMALLY CLOSED**
Date: 2026-09-30

Implementation commit:

- `a5052a9` — Implement V2-A durable runtime and qualification harness

Prospective allowlist amendment:

- `ffaa60b` — Ollama, llama.cpp tool family, CMake/CTest/Ninja, uv, FFmpeg added to the default durable executable allowlist. General-purpose shells remain excluded.

## Delivered runtime

- SQLite WAL bootstrap + schema v1 checksum enforcement.
- Durable operation repository with request-hash idempotency and conflict detection.
- Durable jobs, append-only events and subscriber ACK cursors.
- Detached RemoteMCP worker with launch barrier:
  `worker.json -> DB launch commitment -> launch.commit -> payload`.
- Windows process fingerprint: PID + process creation FILETIME + executable + command hash.
- Conservative startup reconciliation and no auto-relaunch of LOST jobs.
- Durable log/result filesystem.
- Exact `job_submit/get/wait/logs/result/cancel` MCP tools.
- Terminal delivery: at-least-once until explicit idempotent ACK.
- Legacy `run_command` keeps its old public semantics and now passes a minimal safe Windows environment instead of PATH+HOME only.
- Default durable commands include the existing development commands plus Ollama, the observed llama.cpp tool family, CMake, CTest, Ninja, uv and FFmpeg.

## Engineering findings closed during implementation

### Windows atomic metadata replace

Stress testing found transient `WinError 5` when the supervisor read `worker.json` during worker atomic replacement.

Fix:

- unique temporary metadata path;
- fsync when supported;
- bounded retry around `os.replace`;
- still finishes with atomic replacement.

Regression test added.

### Launch nonce CLI ambiguity

`secrets.token_urlsafe()` can begin with `-`, which can be interpreted by argparse as an option value boundary.

Fix:

- all launch nonces use `ln_` prefix;
- worker CLI contract remains unchanged.

Regression test added.

### Short-job terminal publication race

A worker may stop being observable immediately before `terminal.json` becomes visible.

Fix:

- a bounded 2-second stale-identity grace prevents a live supervisor from classifying this narrow publication window as LOST;
- stale/unverifiable identity still resolves conservatively to LOST.

## Test evidence

### V2-0 regression

```text
17 passed
```

### V2-A implementation matrix

```text
27 passed
```

Covers migration/bootstrap, checksum mismatch, operations/idempotency/conflicts, events/ACK, repository state, PID reuse, worker launch barrier, reconciliation, duplicate submit, cancellation, bounded waits, exact tool signatures, safe environment and Windows race regressions.

### Static implementation validation

PASS:

- exact durable module layout;
- migration byte identity;
- forbidden dependency / shell scan;
- exact `job_*` signatures;
- requested default project-tool allowlist.

### OAuth integration

PASS end-to-end:

- OAuth metadata;
- protected-resource metadata;
- DCR;
- PKCE;
- explicit resource binding;
- token exchange;
- MCP initialize;
- tool list with 12 tools;
- path traversal protection;
- command allowlist rejection;
- refresh rotation;
- revoke;
- brute-force lockout.

### Public tunnel smoke

PASS:

- OAuth authorization metadata;
- protected-resource metadata;
- unauthenticated `/mcp` -> 401 Bearer challenge.

## Qualification gates

### Duplicate submit

**PASS**

- same operation ID returned one job ID;
- replay flag true on duplicate;
- exactly one payload execution;
- exactly one job row.

### Restart recovery

**PASS**

- server-side service stopped while payload was RUNNING;
- new service reconciled the existing job;
- same payload PID/start token survived;
- exactly one payload execution;
- final state SUCCEEDED.

### Default project tools

**PASS**

Durable execution successfully ran:

- Ollama 0.34.2
- llama-cli 0.4.1-dev / build 10964
- CMake 4.4.3
- CTest 4.4.3
- Ninja 1.13.2
- uv 0.11.2
- FFmpeg 8.1.1

### Durable 610-second job

**PASS**

Job:

`job_4a0cda97bac669f38e59a87b8514080b`

Evidence:

- same durable job survived after originating service shutdown;
- final state: `SUCCEEDED`;
- exit code: `0`;
- recorded duration: **640,732 ms**;
- terminal event ID: `2`;
- stdout contains both `LONG_START` and `LONG_DONE`.

This exceeds the preregistered minimum gate of 610,000 ms.

## Deliberate V2-A boundaries

Not claimed by this phase:

- multi-agent task/lease registry;
- Git worktree isolation;
- Context Broker/resume package;
- native MCP Tasks extension;
- active-active multiple RemoteMCP supervisors sharing one runtime directory.

Legacy `write_file/edit_file` remain compatibility tools; durable multi-agent mutation isolation will require an explicitly locked CAS/workspace contract rather than silently changing these tools.

## Final verdict

**V2-A PASS**

All preregistered implementation/qualification gates passed:

1. durable 610-second qualification PASS;
2. final V2-A matrix: 27/27 PASS;
3. frozen V2-0 regression: 17/17 PASS;
4. static implementation validator PASS;
5. full OAuth integration PASS;
6. public tunnel smoke PASS;
7. duplicate-submit PASS;
8. restart-recovery PASS;
9. requested project-tool execution PASS.

README and closure manifest are updated in the formal-close commit.

The currently running production endpoint on port 8099 is not automatically replaced by this repository closure; deployment/connector refresh is a separate operational action.