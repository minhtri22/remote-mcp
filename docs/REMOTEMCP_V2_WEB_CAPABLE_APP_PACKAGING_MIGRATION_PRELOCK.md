# RemoteMCP V2 Web-Capable App Packaging — Migration Prelock

Status: **PRELOCKED / SOURCE-STAGED ONLY**

This document freezes the packaging migration needed to make RemoteMCP V2 usable from ChatGPT web without accidentally breaking the existing Desktop-only direct-MCP package.

## Problem

The current RemoteMCP V2 private plugin declares MCP servers directly through `.mcp.json` and `mcp.json`.

ChatGPT treats imported plugins that directly declare MCP servers as **Desktop only**, even when the server URL is a remote HTTPS endpoint.

Therefore:

- the current package is valid for ChatGPT Desktop;
- the same package is not the correct packaging form for ChatGPT web;
- merely adding `.app.json` while keeping direct MCP declarations does not remove the Desktop-only restriction.

## Locked packaging model

The builder exposes two mutually exclusive targets.

### 1. `desktop-direct-mcp`

Purpose: preserve the current direct RemoteMCP connection for Desktop.

Required contents:

```text
.codex-plugin/plugin.json
.mcp.json
mcp.json
plugin.json
skills/remote-mcp/SKILL.md
commands/*.md
```

Requirements:

- `.codex-plugin/plugin.json` declares `mcpServers: "./.mcp.json"`;
- `.mcp.json` and `mcp.json` contain the self-hosted HTTPS RemoteMCP MCP URL;
- no `.app.json` is packaged;
- this target is expected to remain Desktop only.

### 2. `web-app-ref`

Purpose: package RemoteMCP V2 around an existing eligible ChatGPT App / registered MCP app so the plugin itself does not directly declare MCP servers.

Required contents:

```text
.codex-plugin/plugin.json
.app.json
plugin.json
skills/remote-mcp/SKILL.md
commands/*.md
```

Forbidden contents:

```text
.mcp.json
mcp.json
```

Requirements:

- `.codex-plugin/plugin.json` declares `apps: "./.app.json"`;
- `plugin.json` uses `extensions.com.openai.apps: "./.app.json"`;
- `.app.json` maps the `remote` alias to exactly one eligible app ID;
- the app ID must begin with `asdk_app_`, `connector_`, or `templated_apps_`;
- `plugin_asdk_app_...` input is normalized to `asdk_app_...`;
- the builder fails closed if no eligible app ID is supplied;
- no direct MCP declaration may appear in the web package.

## App creation boundary

An app reference does **not** create an app and does not grant access.

The referenced app must already exist and be available to the current ChatGPT account/workspace. Authentication and action permissions remain those of the referenced app.

Current account evidence (2026-10-03):

- ChatGPT Sites can host MCP tools implemented by the Site itself;
- the available Sites capability for this account could not attach the existing RemoteMCP endpoint with its existing OAuth flow;
- Sites reported that the required connector-eligibility capability was unavailable and that only allowed plugins may be used;
- therefore a Site is **not** a valid bridge to the existing RemoteMCP deployment for this account;
- no Site, app, plugin, or credential was created during that attempt.

This is an account/capability boundary, not a RemoteMCP server failure.

Do not present ChatGPT Sites as a default bridge for the existing RemoteMCP endpoint unless an actual account/workspace exposes the required external-MCP connection capability and that capability is verified before packaging.

For full custom MCP apps with write/modify actions, the currently documented ChatGPT route is Business/Enterprise/Edu Developer Mode. Pro can have more limited custom-MCP access, but that does not satisfy RemoteMCP's full write/execute surface. A personal Plus account should not be assumed to have a web-capable external-MCP app-creation path.

Therefore this prelock does not invent or hard-code an app ID.

## Production safety rule

The currently installed RemoteMCP V2 private plugin remains unchanged until all of the following are true:

1. an eligible real app exists;
2. its exact app ID is captured;
3. a `web-app-ref` package is built from the frozen builder;
4. package inspection confirms no `mcp.json` or `.mcp.json`;
5. the package validates successfully;
6. the app is authorized;
7. web usage is verified in a fresh ChatGPT web conversation.

Do not overwrite the current production plugin merely to remove the Desktop-only badge.

## Static acceptance criteria

The migration prelock passes only if source tests establish:

- desktop target still contains direct MCP manifests;
- web target contains `.app.json`;
- web target contains no direct MCP manifests;
- native web manifest references `./.app.json`;
- portable OpenAI extension references `./.app.json`;
- invalid or missing app IDs fail closed;
- command and skill sources are identical across both targets;
- no private deployment URL or real app ID is committed.

## Example build commands

Desktop package:

```powershell
python scripts/build_chatgpt_plugin.py \
  --target desktop-direct-mcp \
  --url https://mcp.example.com
```

Web package after a real app ID exists:

```powershell
python scripts/build_chatgpt_plugin.py \
  --target web-app-ref \
  --app-id asdk_app_example
```

Environment equivalent:

```text
REMOTEMCP_CHATGPT_APP_ID=asdk_app_example
```

## Explicit non-goals

This migration does not:

- restart the RemoteMCP gateway;
- modify OAuth state;
- pair or revoke execution devices;
- change project/task routing;
- modify scientific jobs;
- create a replacement app ID;
- attempt to bypass ChatGPT plan/workspace app eligibility;
- claim Sites can bridge an external MCP endpoint unless that capability is actually available;
- claim web compatibility before an actual web verification succeeds.
