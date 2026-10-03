# RemoteMCP V2 Web app-backed identity freeze

Status: SOURCE GUARD IMPLEMENTED — pending merge and live-plugin recovery.

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
