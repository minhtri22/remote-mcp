# RemoteMCP V2 Web-Capable App Packaging — Static QA Closure

Date: 2026-10-03

Gate:

`REMOTEMCP_V2_WEB_CAPABLE_APP_PACKAGING_AUDIT_AND_MIGRATION_PRELOCK`

Verdict:

`WEB_MIGRATION_PASS`

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

## Sites bridge attempt

A ChatGPT Sites attempt was performed to create a Web bridge around the existing RemoteMCP endpoint.

Observed result:

- the existing RemoteMCP HTTPS MCP endpoint was recognized;
- the Sites capability available to this account could not attach that remote endpoint using its existing OAuth flow;
- the connector-eligibility tool required to reuse RemoteMCP was unavailable;
- Sites enforced an `allowed plugins` boundary;
- no Site or plugin was created;
- no credential was stored.

Adjudication:

`SITES_EXTERNAL_REMOTEMCP_BRIDGE_NOT_AVAILABLE_ON_CURRENT_ACCOUNT_SURFACE`

This is not a failure of the RemoteMCP endpoint. It is a ChatGPT account/capability boundary.

## Remaining prerequisite for Web release

The Web target requires one real eligible ChatGPT App that can expose the existing RemoteMCP tool surface on ChatGPT web.

For the full RemoteMCP write/execute surface, the currently supported direct custom-MCP path requires an eligible managed workspace with full MCP support. Do not assume that a personal Plus account can create that app.

The existing personal-plugin inventory contains RemoteMCP plugins only; it does not provide an eligible app ID for `web-app-ref`.

Do not invent an app ID and do not upload a Web package until the referenced app exists and can be authorized.

## Next gate

When an eligible app-creation surface becomes available:

`REMOTEMCP_V2_WEB_APP_REFERENCE_MATERIALIZATION_AND_FRESH_WEB_VERIFICATION`

That gate must:

1. create or select the eligible app through a supported ChatGPT app-creation path;
2. capture its exact app ID;
3. build `web-app-ref`;
4. verify ZIP contents contain `.app.json` and no direct MCP manifests;
5. run executable package tests when a runner is available;
6. install/update a test plugin;
7. authorize the underlying app;
8. verify a live `device_list` call from a fresh ChatGPT Web conversation;
9. only then authorize replacement/update of the production RemoteMCP V2 plugin.

Until that prerequisite exists, keep the current Desktop package and production plugin unchanged.


## Fresh Web verification closure

On 2026-10-03, a clean private sibling plugin was created:

- plugin name: `remote-mcp-v2-web`;
- display name: `RemoteMCP V2 Web`;
- version: `1.3.0`;
- packaging: app-backed;
- dependency: the user's existing RemoteDesktop Apps SDK app;
- direct MCP manifests: absent.

The plugin detail page showed:

```text
Apps 1
└── RemoteDesktop
    └── Connected
```

A fresh ChatGPT Web conversation launched from **Try in chat** successfully invoked live RemoteMCP device inventory through the app-backed path.

Observed result:

```text
registered devices: 5
ONLINE: 2
OFFLINE: 3
```

This proves all of the following together:

- ChatGPT Web can load the `RemoteMCP V2 Web` wrapper;
- the wrapper resolves the existing RemoteDesktop app dependency;
- the existing OAuth/account connection is usable;
- the full RemoteMCP tool surface is available through the underlying app;
- live gateway calls succeed from a fresh Web conversation;
- direct `.mcp.json` / `mcp.json` packaging is not required for the Web wrapper.

Final verdict:

`REMOTEMCP_V2_WEB_FRESH_CHAT_WRAPPER_VERIFICATION = PASS`

The legacy `RemoteDesktop` app-backed plugin remains the underlying app/OAuth carrier.

The existing `RemoteMCP V2` Desktop direct-MCP plugin remains unchanged and must not be removed until a later explicit cleanup/migration decision.

Scientific jobs and execution-device state were not mutated by this verification.
