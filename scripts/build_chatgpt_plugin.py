from __future__ import annotations

import argparse
import json
import os
import zipfile
from pathlib import Path
from urllib.parse import urlparse


def normalize_mcp_url(value: str) -> str:
    raw = (value or "").strip().rstrip("/")
    if not raw:
        raise ValueError("RemoteMCP gateway URL is required (--url or PUBLIC_URL)")
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("ChatGPT plugin endpoint must be a public HTTPS URL")
    if parsed.path in ("", "/"):
        return raw + "/mcp"
    if parsed.path.rstrip("/").endswith("/mcp"):
        return raw
    return raw + "/mcp"


def _json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def package_files(mcp_url: str, version: str) -> dict[str, str]:
    description = "RemoteMCP connector with durable multi-device routed execution and operator commands."
    interface = {
        "displayName": "RemoteMCP",
        "shortDescription": "Self-hosted RemoteMCP routed execution",
        "longDescription": (
            "Work with a self-hosted RemoteMCP gateway, including durable jobs, "
            "multi-agent tasks, and multi-device execution routing."
        ),
        "defaultPrompt": "List my RemoteMCP execution devices and their status.",
    }
    skill = """---
name: remote-mcp
description: Use the full RemoteMCP V2 surface, including multi-device execution routing.
---

Use `device_list` and `device_status` to inspect execution nodes.
If no execution devices are paired, local project tools may be used on the gateway.
For multi-device work, use `project_register_on_device` or `project_bind_device` to choose placement.
Before registering a project again, reuse an existing project/task when the user or prior context already identifies it.
Tasks inherit immutable project-to-device placement; do not try to override a task's device.
Use `task_read_file`, CAS mutation tools, and `task_job_*` for routed task work.
Legacy `run_command`, `read_file`, and `write_file` are gateway-local compatibility tools and are not evidence of execution routing.
When managed projects exist, prefer task-scoped tools over legacy execution/mutation tools.

Operator command aliases:
- `/status`: inspect gateway-visible device status using `device_list` and `device_status`.
- `/restart <device>`: restart one execution node using `device_restart`.
  If more than one device exists and no target is supplied, ask the user to choose; never guess.
  If the target reports active routed jobs, do not set `allow_active_jobs=true` without explicit user confirmation.
  After restart, verify the same device_id, fingerprint, and route_generation.
- `/restart gateway`: explain that an unresponsive gateway cannot restart itself through the same MCP endpoint; use the host/out-of-band restart path instead.

If a client does not expose a native slash-command picker but sends these strings as ordinary chat text, follow the same semantics.
"""
    return {
        ".codex-plugin/plugin.json": _json({
            "interface": interface,
            "name": "remote-mcp",
            "version": version,
            "description": description,
            "author": {"name": "RemoteMCP OSS"},
            "keywords": ["mcp", "remote", "multi-device", "self-hosted"],
            "skills": "./skills",
            "mcpServers": "./.mcp.json",
        }),
        ".mcp.json": _json({
            "mcpServers": {
                "remote": {
                    "type": "streamable-http",
                    "url": mcp_url,
                    "headers": {},
                }
            }
        }),
        "mcp.json": _json({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
            "mcpServers": {
                "remote": {
                    "type": "streamable-http",
                    "url": mcp_url,
                }
            },
        }),
        "plugin.json": _json({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "remote-mcp",
            "version": version,
            "description": description,
            "extensions": {"com.openai": {"interface": interface}},
        }),
        "skills/remote-mcp/SKILL.md": skill,
        "commands/status.md": """---
description: Show RemoteMCP gateway-visible execution-device status without changing state.
---

# RemoteMCP Status

## Preflight

Use RemoteMCP MCP tools only. Do not run local shell commands as a substitute.

## Plan

1. Call `device_list`.
2. For each returned device, call `device_status` when available.
3. Report ONLINE/OFFLINE/REVOKED state, hostname, device id, route generation, bound projects, active commands, and active routed jobs.

## Commands

This command is read-only.

## Verification

Do not claim the endpoint is healthy merely because cached identity metadata exists. At least one live MCP tool response must succeed.

## Summary

Return a compact status table and identify any device that is offline, busy, or carrying active routed jobs.
""",
        "commands/restart.md": """---
description: Restart one RemoteMCP execution node safely and verify the same device identity returns.
---

# Restart RemoteMCP Execution Node

## Preflight

1. Call `device_list`.
2. Resolve `$ARGUMENTS` against exact device_id, device name, or hostname.
3. If there is more than one device and no unique target was supplied, ask the user to choose. Never guess.
4. Call `device_status` for the selected target.
5. If `active_routed_jobs > 0`, do not restart unless the user explicitly confirms interruption of those jobs.
6. If the requested target is `gateway`, do not pretend this command can recover a dead endpoint. Explain that gateway recovery is out-of-band.

## Plan

Restart exactly one execution node. Preserve its existing runtime identity, key fingerprint, and route generation.

## Commands

Call `device_restart` with a fresh operation_id.

- Default: `allow_active_jobs=false`.
- Set `allow_active_jobs=true` only after explicit confirmation when active routed jobs exist.

Then poll `device_status` until the same device id is live again or the verification window is exhausted.

## Verification

PASS only if:
- the target returns ONLINE;
- device_id is unchanged;
- key fingerprint is unchanged;
- route_generation is unchanged.

Do not pair a replacement device as a restart fallback.

## Summary

Report the selected device, restart acceptance, and identity verification result.
""",
    }


def build_archive(gateway_url: str, output: Path, version: str = "1.0.0") -> Path:
    mcp_url = normalize_mcp_url(gateway_url)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = package_files(mcp_url, version)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in sorted(files.items()):
            zf.writestr(name, content)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a private ChatGPT plugin archive for one RemoteMCP gateway."
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("PUBLIC_URL", ""),
        help="RemoteMCP public HTTPS origin, e.g. https://mcp.example.com",
    )
    parser.add_argument(
        "--out",
        default="dist/remote-mcp-chatgpt-plugin.zip",
        help="Output ZIP path.",
    )
    parser.add_argument("--version", default="1.1.0", help="Plugin package version.")
    args = parser.parse_args()

    try:
        path = build_archive(args.url, Path(args.out), args.version)
    except ValueError as exc:
        parser.error(str(exc))
        return
    print(path)


if __name__ == "__main__":
    main()
