# RemoteMCP V2 Web-Capable App Packaging — Static QA Closure

Date: 2026-10-03

Gate:

`REMOTEMCP_V2_WEB_CAPABLE_APP_PACKAGING_AUDIT_AND_MIGRATION_PRELOCK`

Verdict:

`SOURCE_STATIC_PRELOCK_PASS / WEB_RELEASE_NOT_YET_AUTHORIZED`

## What passed

The source builder now has two mutually exclusive packaging targets:

- `desktop-direct-mcp`
- `web-app-ref`

Static source inspection confirms:

- the stable plugin identity is `remote-mcp-v2bd`;
- the Desktop target declares `.mcp.json` and `mcp.json`;
- the Web target declares `.app.json`;
- the Web target does not package direct MCP manifests;
- the native Web manifest references `apps: "./.app.json"`;
- the portable OpenAI extension references `apps: "./.app.json"`;
- Web packaging requires an app ID with an eligible prefix;
- `plugin_asdk_app_...` input is normalized to `asdk_app_...`;
- missing or invalid app IDs fail closed;
- no real deployment app ID is committed;
- no private deployment URL was added by this migration;
- command files and skill wiring remain first-class sources.

## Executable QA status

Pytest coverage was updated to verify Desktop/Web package isolation, app-ID normalization, fail-closed behavior, and command-source preservation.

A temporary external QA runner was attempted twice. The runners were unavailable independently of this repository:

- one returned HTTP 429 during the executor probe;
- the alternate runner returned HTTP 404 during the executor probe.

Therefore this closure does **not** claim executable pytest PASS. The current verdict is deliberately limited to source/static prelock PASS.

No QA files or directories were created on the user's machines.

## Production hold

The currently installed personal plugin remains:

- name: `remote-mcp-v2bd`;
- version: `1.2.0`;
- scope: `USER`;
- discoverability: `PRIVATE`.

It was not updated or replaced during this prelock.

No gateway, OAuth state, execution device, project, task, or scientific job was changed.

## Remaining prerequisite for Web release

The Web target requires one real eligible ChatGPT App / Site-backed app ID.

The currently owned personal-plugin inventory contains the existing RemoteMCP plugins only; no eligible Site/App reference is presently available for this migration.

Do not invent an app ID and do not upload a Web package until the referenced app exists and can be authorized.

## Next gate

After an eligible app exists:

`REMOTEMCP_V2_WEB_APP_REFERENCE_MATERIALIZATION_AND_FRESH_WEB_VERIFICATION`

That gate must:

1. capture the exact eligible app ID;
2. build `web-app-ref`;
3. verify ZIP contents contain `.app.json` and no direct MCP manifests;
4. run executable package tests when a runner is available;
5. install/update a test plugin;
6. authorize the underlying app;
7. verify a live `device_list` call from a fresh ChatGPT Web conversation;
8. only then authorize replacement/update of the production RemoteMCP V2 plugin.
