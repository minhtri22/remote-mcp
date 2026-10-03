# RemoteMCP V2 Web-Capable App Packaging — Static QA Closure

Date: 2026-10-03

Gate:

`REMOTEMCP_V2_WEB_CAPABLE_APP_PACKAGING_AUDIT_AND_MIGRATION_PRELOCK`

Verdict:

`WEB_MIGRATION_PASS`

## What passed

The source builder exposes two mutually exclusive packaging targets:

- `desktop-direct-mcp`
- `web-app-ref`

Static source inspection confirms:

- the Desktop target declares `.mcp.json` and `mcp.json`;
- the Web target declares `.app.json`;
- the Web target does not package direct MCP manifests;
- the native Web manifest references `apps: "./.app.json"`;
- the portable OpenAI extension references `apps: "./.app.json"`;
- Web packaging requires an eligible app ID supplied at build time;
- missing or invalid app IDs fail closed;
- no real deployment app ID is committed;
- no private deployment URL is committed;
- command files and skill wiring remain first-class sources.

All public examples use placeholders such as:

```text
https://mcp.example.com
<CHATGPT_APP_ID>
<PRIVATE_PLUGIN_ID>
<CONNECTED_ACCOUNT_NAME>
```

## Executable QA boundary

Package tests cover Desktop/Web isolation, app-ID normalization, fail-closed behavior, and command-source preservation.

A temporary external QA runner was unavailable during the initial source-only prelock, so that stage did not claim executable pytest PASS.

No QA files or directories were created on operator machines by the external-runner attempts.

## Production safety rule

The Web migration does not require overwriting an existing direct-MCP Desktop package.

The safe migration pattern is:

```text
existing app-backed OAuth carrier
          ↓
clean web wrapper (.app.json)
          ↓
fresh ChatGPT Web verification
```

Do not place real app IDs, connected-account names, OAuth values, private endpoints, machine names, device IDs, or local paths in public release records.

## Fresh Web verification closure

A clean Web wrapper package was created with:

- a distinct public plugin identity;
- app-backed packaging;
- no direct `.mcp.json` or `mcp.json`;
- the same public skill/command sources as the V2 package.

The plugin detail surface showed an app dependency in the Connected state, and a fresh ChatGPT Web conversation successfully invoked a live read-only RemoteMCP device inventory through the app-backed path.

For privacy, the public record intentionally omits:

- the real app/plugin identifier;
- connected-account display name;
- private MCP endpoint;
- device IDs and hostnames;
- device counts and live ONLINE/OFFLINE state.

This proves:

- ChatGPT Web can load the Web wrapper;
- the wrapper can resolve an eligible app dependency;
- the OAuth/account connection can be reused;
- the full RemoteMCP tool surface can be exposed through the underlying app;
- live gateway calls can succeed from a fresh Web conversation;
- direct MCP manifests are not required in the Web wrapper.

Final verdict:

`REMOTEMCP_V2_WEB_FRESH_CHAT_WRAPPER_VERIFICATION = PASS`

Operational deployment values remain private and are not part of this public QA record.
