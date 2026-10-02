# V2-B Production Release — 2026-09-30

Status: **PASS**

This is an operational deployment record. It does not alter the scientific/engineering qualification verdict of V2-B.

## Source and release snapshot

- source commit: `673d09fc74c8a248ca52cfab7bf47dd68226f9b8`
- source repo: `<LOCAL_SOURCE_ROOT>`
- immutable runtime snapshot: `<PRIVATE_RELEASE_ROOT>\673d09f`

Previous production:

- cwd: `<PRIVATE_PREVIOUS_RELEASE_ROOT>`
- PID: `28464`
- listener: `127.0.0.1:8099`

Current production:

- cwd: `<PRIVATE_RELEASE_ROOT>\673d09f`
- PID: `11372`
- listener: `127.0.0.1:8099`
- public endpoint: `https://mcp.example.com`

## Persistent state

OAuth state is explicitly pinned to:

`<PRIVATE_STATE_ROOT>\oauth-state.json`

V2 runtime state is explicitly pinned to:

`<PRIVATE_STATE_ROOT>\runtime`

The runtime database reports migrations:

```text
1
2
```

At cutover completion the projects table contained **0** V2-B projects. Therefore managed mode remained inactive and compatibility tools were not globally disabled during the release.

Rollback material is retained under:

`<PRIVATE_BACKUP_ROOT>\pre-v2b`

## Preflight

Before touching production, the release snapshot was started in isolation on port 8101 with production-equivalent environment and copied OAuth state.

PASS:

- candidate process started;
- TCP listener 8101;
- OAuth authorization-server metadata;
- unauthenticated `/mcp` -> 401;
- schema migrations [1, 2].

The first health helper attempt reported FAIL only because its own stripped child environment could not initialize Windows networking; Uvicorn logs showed the candidate had actually started. The helper was corrected to probe through a child using the full production environment, after which the same preflight passed.

## Cutover protocol

A detached local helper:

1. verified PID 28464 still owned port 8099 and cwd `<PRIVATE_PREVIOUS_RELEASE_ROOT>`;
2. captured the existing process environment without printing secrets;
3. stopped only PID 28464;
4. copied the latest OAuth state into the explicit production state path;
5. started the release snapshot on the same local port 8099;
6. checked listener, OAuth metadata and 401 challenge;
7. verified migration versions [1, 2];
8. would automatically restart the untouched old source on failure.

Rollback was not needed.

## Post-cutover QA

PASS:

- local listener: PID 11372 on 127.0.0.1:8099;
- process cwd matches release snapshot;
- public OAuth metadata;
- protected-resource metadata;
- public `/mcp` unauthenticated -> 401 Bearer challenge;
- runtime migrations [1, 2];
- current authenticated RemoteDesktop connector continued working after restart, demonstrating OAuth state continuity.

Cloudflare routing was not changed.

## Current operational boundary

Do not register the first production V2-B managed project until the consuming client has refreshed the new MCP tool schema and can use task-scoped tools.

Once any V2-B project is registered, managed mode intentionally blocks:

- legacy `run_command`;
- direct V2-A `job_submit`;
- legacy writes inside registered project/worktree scope.

The next architecture phase remains:

`REMOTE_MCP_V2BD_MULTI_DEVICE_ROUTING_PRELOCK`

## Two-device connector finding

Production OAuth/tool-schema probing after cutover established:

- production MCP exposes **32 tools** through a fresh OAuth-bound `tools/list`;
- required V2-B tools such as `agent_register`, `agent_heartbeat`, `task_claim`, `file_write_cas` and `task_job_submit` are live;
- the currently open ChatGPT conversation on machine 2 still exposes the previously cached 6-tool connector schema;
- OpenAI MCP app updates therefore require a client-side tool refresh before the new actions become available in that ChatGPT session.

The OAuth registry currently contains one real `ChatGPT` DCR client (plus test/Codex clients). This indicates that the ChatGPT app connection is account-level and can be shared across login devices.

Therefore physical-device identity MUST NOT require distinct OAuth `auth_client_id` values.

Correct production smoke criterion:

```text
same owner_account_id
+ distinct client_instance_id
+ distinct session_id
+ both sessions ACTIVE concurrently
```

The two physical devices may legitimately share the same `auth_client_id`.

Status at this record:

- machine 1 -> production V2-B: PASS;
- machine 2 -> production V2-B: PASS;
- production 32-tool schema: PASS;
- machine-2 conversation refreshed to 32 tools: PENDING client-side Refresh;
- two physical-device `agent_register` sessions: PENDING client-side Refresh;
- managed project registration: NOT STARTED.
