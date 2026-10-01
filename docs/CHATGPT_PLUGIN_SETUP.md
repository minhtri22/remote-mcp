# ChatGPT Plugin Setup

RemoteMCP ships a package builder so a self-hosted user does not need to hand-author plugin manifests or think about the 44-tool schema.

The package contains only connector metadata and the user's own MCP endpoint. It does **not** contain OAuth tokens, passwords, pairing secrets, node keys, or project data.

## 1. Build the package

On the gateway machine, set `PUBLIC_URL` to the public HTTPS origin already used by RemoteMCP:

```powershell
$env:PUBLIC_URL = "https://mcp.example.com"
python scripts/build_chatgpt_plugin.py
```

Or pass it explicitly:

```powershell
python scripts/build_chatgpt_plugin.py --url https://mcp.example.com
```

Output:

```text
dist/remote-mcp-chatgpt-plugin.zip
```

The builder automatically points the plugin to:

```text
https://mcp.example.com/mcp
```

If the supplied URL already ends in `/mcp`, it is used as-is.

## 2. Install in ChatGPT

Create/install a **private plugin** in ChatGPT from the generated ZIP archive.

After the plugin is connected, complete the RemoteMCP OAuth authorization flow when prompted.

The generated package contains:

```text
.codex-plugin/plugin.json
.mcp.json
mcp.json
plugin.json
skills/remote-mcp/SKILL.md
```

The skill tells ChatGPT the important routing rule:

- `device_list` / `device_status` discover execution nodes;
- `project_register_on_device` chooses project placement;
- tasks inherit immutable device placement;
- task-scoped tools perform routed work;
- legacy `run_command`, `read_file`, and `write_file` are gateway-local compatibility tools and are not proof of multi-device routing.

## 3. Operator slash commands

The generated package now includes plugin command files under `commands/`.

Supported staged commands:

```text
/status
/restart <device>
```

`/status` is read-only and uses the RemoteMCP device tools.

`/restart <device>` resolves the requested execution device, checks `active_routed_jobs`, and uses the staged `device_restart` tool. It will not guess a target when several devices exist and will not override active-job protection without explicit user confirmation.

If a client does not expose a native slash-command picker, sending the same text as an ordinary prompt should follow the same command semantics through the packaged skill instructions.

A full gateway timeout is different: if the MCP endpoint itself does not answer, no in-band plugin command can execute. Use the out-of-band recovery/watchdog documented in [GATEWAY_RECOVERY.md](GATEWAY_RECOVERY.md).

## 4. Verify the connector

For multi-device installs, ask ChatGPT:

```text
List my RemoteMCP execution devices and their status.
```

The plugin should expose the full **44-tool** RemoteMCP surface, including:

```text
device_pair_begin
device_list
device_status
device_revoke
project_register_on_device
project_bind_device
task_list_dir
task_read_file
task_search
task_job_get
task_job_logs
task_job_result
```

For a one-computer setup with no paired execution nodes, `device_list` may legitimately be empty.

## 5. When the tool schema changes

ChatGPT conversations can retain the connector schema that was loaded when the conversation started.

After a RemoteMCP release changes the public tool surface:

1. rebuild the plugin archive;
2. update/reconnect the private plugin;
3. start a fresh ChatGPT conversation if an existing conversation still shows the older tool set.

Do not change the RemoteMCP gateway URL unless the deployment URL itself changed.

## 6. Open-source / self-hosted rule

The package builder never hard-codes a RemoteMCP-operated central domain.

Each package is generated from that user's own `PUBLIC_URL`.

RemoteMCP does not require:

- a centrally operated RemoteMCP cloud;
- a RemoteMCP account service;
- access to another user's deployment;
- ChatGPT cookies on an execution node.

The only ChatGPT-facing endpoint is the user's own RemoteMCP MCP URL.

## 7. Package builder options

```text
python scripts/build_chatgpt_plugin.py --help
```

Supported inputs:

- `--url` — RemoteMCP public HTTPS origin or full `/mcp` URL;
- `--out` — output ZIP path;
- `--version` — plugin package version.

If `--url` is omitted, the builder reads `PUBLIC_URL`.
