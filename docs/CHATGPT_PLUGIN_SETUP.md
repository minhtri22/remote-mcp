# ChatGPT Plugin Setup

RemoteMCP ships a package builder so a self-hosted user does not need to hand-author plugin manifests or think about the full tool schema.

The package contains connector/plugin metadata and the user's own RemoteMCP integration reference. It does **not** contain OAuth tokens, passwords, pairing secrets, node keys, or project data.

RemoteMCP V2 has two intentionally separate packaging targets.

## 1. Choose the correct target

### Desktop direct MCP

Use:

```text
desktop-direct-mcp
```

This target directly declares the RemoteMCP MCP server through `.mcp.json` and `mcp.json`.

It is appropriate for ChatGPT Desktop and preserves the existing self-hosted direct-MCP behavior.

Because the plugin directly declares an MCP server, ChatGPT may mark this package **Desktop only**, even though the endpoint is remote HTTPS.

### Web app reference

Use:

```text
web-app-ref
```

This target does **not** package `.mcp.json` or `mcp.json`.

Instead it references an already-created eligible ChatGPT App / registered MCP app through:

```text
.app.json
```

The referenced app owns its connection/authentication/action permissions. The plugin only packages RemoteMCP's skills and operator commands around that app.

An app reference does not create the app. A valid existing app ID is required.

Supported app-ID prefixes:

```text
asdk_app_
connector_
templated_apps_
```

If an app URL or tool exposes an ID beginning with `plugin_asdk_app_`, the builder accepts it and normalizes it to `asdk_app_`.

## 2. Build a Desktop package

On the gateway machine, set `PUBLIC_URL` to the public HTTPS origin already used by RemoteMCP:

```powershell
$env:PUBLIC_URL = "https://mcp.example.com"
python scripts/build_chatgpt_plugin.py --target desktop-direct-mcp
```

Or pass it explicitly:

```powershell
python scripts/build_chatgpt_plugin.py `
  --target desktop-direct-mcp `
  --url https://mcp.example.com
```

Output:

```text
dist/remote-mcp-chatgpt-plugin.zip
```

The builder points the direct MCP package to:

```text
https://mcp.example.com/mcp
```

If the supplied URL already ends in `/mcp`, it is used as-is.

## 3. Build a Web app-reference package

First create or obtain an eligible ChatGPT App and capture its exact app ID.

Then build:

```powershell
python scripts/build_chatgpt_plugin.py `
  --target web-app-ref `
  --app-id asdk_app_example
```

Or use:

```powershell
$env:REMOTEMCP_CHATGPT_APP_ID = "asdk_app_example"
python scripts/build_chatgpt_plugin.py --target web-app-ref
```

The web package contains:

```text
.codex-plugin/plugin.json
.app.json
plugin.json
skills/remote-mcp/SKILL.md
commands/status.md
commands/devices.md
commands/restart.md
```

It intentionally does **not** contain:

```text
.mcp.json
mcp.json
```

This fail-closed separation matters: keeping a direct MCP declaration in the same package can preserve the Desktop-only classification.

## 4. ChatGPT app eligibility boundary

A `web-app-ref` package requires an actual eligible ChatGPT App that is available to the installing account/workspace.

Do not assume that a generic Site or plugin can bridge an arbitrary external MCP endpoint. The underlying ChatGPT app must already support the required RemoteMCP connection/authentication model and tool surface.

Keep all account- and deployment-specific identifiers private:

```text
<CHATGPT_APP_ID>
<PRIVATE_PLUGIN_ID>
<CONNECTED_ACCOUNT_NAME>
<PRIVATE_MCP_ENDPOINT>
```

Do not invent an app ID, reuse a plugin ID as an app ID, or commit a real app/plugin identifier to the public repository.

## 5. Install or update in ChatGPT

Create/install the private plugin from the generated ZIP archive.

After the referenced app or direct MCP connection is available, complete its required authorization.

For an existing plugin update, verify the package target before upload:

- Desktop package: direct MCP manifests are expected.
- Web package: `.app.json` is expected and direct MCP manifests are forbidden.

The source migration does not automatically update an already-installed plugin.

## 6. Operator slash commands

The generated package includes first-class command files under `commands/`.

Supported commands:

```text
/status
/devices
/restart <device>
```

`/status` is read-only and gives a compact health summary.

`/devices` is read-only and lists the complete gateway-visible execution-device inventory using `device_list`.

`/restart <device>` resolves the requested execution device, checks `active_routed_jobs`, and uses the staged `device_restart` tool. It will not guess a target when several devices exist and will not override active-job protection without explicit user confirmation.

If a client does not expose a native slash-command picker, sending the same text as an ordinary prompt should follow the same command semantics through the packaged skill instructions.

A full gateway timeout is different: if the MCP endpoint itself does not answer, no in-band plugin command can execute. Use the out-of-band recovery/watchdog documented in [GATEWAY_RECOVERY.md](GATEWAY_RECOVERY.md).

## 7. Verify the connector

For multi-device installs, ask ChatGPT:

```text
List my RemoteMCP execution devices and their status.
```

A successful web migration requires a **fresh ChatGPT web conversation** to invoke a live RemoteMCP tool through the referenced app. Seeing the plugin in the directory is not sufficient proof.

For a one-computer setup with no paired execution nodes, `device_list` may legitimately be empty.

## 8. When the tool schema changes

ChatGPT conversations can retain the connector schema that was loaded when the conversation started.

After a RemoteMCP release changes the public tool surface:

1. refresh/update the underlying app connection where applicable;
2. rebuild the plugin archive;
3. update/reconnect the private plugin;
4. start a fresh ChatGPT conversation if an existing conversation still shows the older tool set.

Do not change the RemoteMCP gateway URL unless the deployment URL itself changed.

## 9. Open-source / self-hosted rule

The builder never hard-codes a RemoteMCP-operated central domain.

Each Desktop package is generated from that user's own `PUBLIC_URL`.

Each Web package is generated from that user's own eligible ChatGPT app ID.

RemoteMCP does not require:

- a centrally operated RemoteMCP cloud;
- a RemoteMCP account service;
- access to another user's deployment;
- ChatGPT cookies on an execution node.

## 10. Builder options

```text
python scripts/build_chatgpt_plugin.py --help
```

Supported inputs:

- `--target` — `desktop-direct-mcp` or `web-app-ref`;
- `--url` — public HTTPS RemoteMCP origin for Desktop direct MCP;
- `--app-id` — existing eligible app ID for Web app-reference packaging;
- `--out` — output ZIP path;
- `--version` — plugin package version.

Migration prelock: [REMOTEMCP_V2_WEB_CAPABLE_APP_PACKAGING_MIGRATION_PRELOCK.md](REMOTEMCP_V2_WEB_CAPABLE_APP_PACKAGING_MIGRATION_PRELOCK.md).

Static QA closure: [REMOTEMCP_V2_WEB_CAPABLE_APP_PACKAGING_STATIC_QA_CLOSURE.md](REMOTEMCP_V2_WEB_CAPABLE_APP_PACKAGING_STATIC_QA_CLOSURE.md).
