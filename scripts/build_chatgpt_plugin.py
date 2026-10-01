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
    description = "RemoteMCP connector with the full 44-tool multi-device routing surface."
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
Tasks inherit immutable project-to-device placement; do not try to override a task's device.
Use `task_read_file`, CAS mutation tools, and `task_job_*` for routed task work.
Legacy `run_command`, `read_file`, and `write_file` are gateway-local compatibility tools and are not evidence of execution routing.
When managed projects exist, prefer task-scoped tools over legacy execution/mutation tools.
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
    parser.add_argument("--version", default="1.0.0", help="Plugin package version.")
    args = parser.parse_args()

    try:
        path = build_archive(args.url, Path(args.out), args.version)
    except ValueError as exc:
        parser.error(str(exc))
        return
    print(path)


if __name__ == "__main__":
    main()
