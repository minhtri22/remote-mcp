# RemoteMCP V2 Web app-backed identity freeze

Status: SOURCE GUARD MERGED — live-plugin recovery pending.

## Frozen contract

The plugin identity `remote-mcp-v2-web` is permanently app-backed.

A valid package for this identity MUST:

- contain `.app.json`;
- set `.codex-plugin/plugin.json` to `"apps": "./.app.json"`;
- omit `.mcp.json`;
- omit `mcp.json`;
- omit `mcpServers` from the native manifest.

The identity MUST NOT be built with the `desktop-direct-mcp` target.

## Builder guard

`scripts/build_chatgpt_plugin.py` fails closed when:

```text
plugin_name == remote-mcp-v2-web
AND
target != web-app-ref
```

The CLI also requires an explicit `--target`; there is no silent Desktop default.

## Regression coverage

`tests/test_chatgpt_plugin_builder.py` verifies:

1. the Web identity rejects the Desktop target;
2. the Web identity still produces only the app-backed package shape;
3. app-backed output contains no direct-MCP manifests.

## Operational rule

Before any live update of the Web plugin, inspect the package/archive first. Never use a direct-MCP archive for the Web identity.

This document intentionally contains no private hostnames, paths, account identifiers, plugin identifiers, app identifiers, tokens, pairing secrets, or deployment URLs.


## Live identity recovery status — 2026-10-03

Source hardening is complete, but the original live Web plugin identity is not yet clean.

Read-back of the current release confirms that direct-MCP package files are still present. The account-plugin editor available to agents uses overlay semantics and cannot delete files, even when given a complete archive. Therefore an agent-side `update_plugin` cannot satisfy the frozen contract for the already-regressed identity.

A separate clean recovery package/plugin may be used for validation, but it MUST NOT be treated as restoration of the original Web identity because its backend identity is different.

The live gate remains closed until the original plugin is changed through a true full-package replace/restore surface that removes the direct-MCP files. After that operation, re-inspection MUST verify all of the following before declaring PASS:

- `.app.json` exists and references the intended existing app;
- `.mcp.json` is absent;
- `mcp.json` is absent;
- native manifest uses `apps: "./.app.json"`;
- native manifest contains no `mcpServers`;
- portable OpenAI extension uses `apps: "./.app.json"`.

Do not open downstream CQG recovery work until this read-back gate passes.
