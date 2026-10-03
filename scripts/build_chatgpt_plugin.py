from __future__ import annotations

import argparse
import json
import os
import re
import zipfile
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
COMMANDS_DIR = REPO_ROOT / "commands"

PLUGIN_NAME = "remote-mcp-v2bd"
DISPLAY_NAME = "RemoteMCP V2"
DEFAULT_VERSION = "1.3.0"

TARGET_DESKTOP_DIRECT_MCP = "desktop-direct-mcp"
TARGET_WEB_APP_REF = "web-app-ref"
TARGETS = (TARGET_DESKTOP_DIRECT_MCP, TARGET_WEB_APP_REF)
WEB_APP_BACKED_PLUGIN_NAME = "remote-mcp-v2-web"

_APP_ID_RE = re.compile(r"^(?:asdk_app_|connector_|templated_apps_)[A-Za-z0-9][A-Za-z0-9_-]*$")


def load_local_public_url(path: Path = REPO_ROOT / ".env") -> None:
    if os.environ.get("PUBLIC_URL") or not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != "PUBLIC_URL":
            continue
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ["PUBLIC_URL"] = value
        return


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


def normalize_app_id(value: str) -> str:
    raw = (value or "").strip()
    if raw.startswith("plugin_asdk_app_"):
        raw = raw[len("plugin_"):]
    if not _APP_ID_RE.fullmatch(raw):
        raise ValueError(
            "web-app-ref requires an eligible app id beginning with "
            "asdk_app_, connector_, or templated_apps_"
        )
    return raw


def validate_target_identity(*, plugin_name: str, target: str) -> None:
    if plugin_name == WEB_APP_BACKED_PLUGIN_NAME and target != TARGET_WEB_APP_REF:
        raise ValueError(
            f"{WEB_APP_BACKED_PLUGIN_NAME} is frozen as app-backed and must use "
            f"--target {TARGET_WEB_APP_REF}; refusing {target}"
        )


def _json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def load_command_files(command_dir: Path = COMMANDS_DIR) -> dict[str, str]:
    command_dir = command_dir.resolve()
    if not command_dir.is_dir():
        raise ValueError(f"RemoteMCP commands directory not found: {command_dir}")

    files: dict[str, str] = {}
    for path in sorted(command_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "\ndescription:" not in text:
            raise ValueError(f"command file is missing YAML description frontmatter: {path.name}")
        files[f"commands/{path.name}"] = text

    if not files:
        raise ValueError("RemoteMCP has no user-facing command files")
    return files


def _interface(display_name: str = DISPLAY_NAME) -> dict:
    return {
        "displayName": display_name,
        "shortDescription": "Self-hosted RemoteMCP routed execution",
        "longDescription": (
            "Work with a self-hosted RemoteMCP gateway, including durable jobs, "
            "multi-agent tasks, multi-device execution routing, and operator commands."
        ),
        "defaultPrompt": "List my RemoteMCP execution devices and their status.",
        "developerName": "RemoteMCP OSS",
        "category": "Other",
        "capabilities": [],
    }


def _skill() -> str:
    return """---
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

Operator commands:
- `/status`: show a compact gateway/device health summary.
- `/devices`: list all gateway-visible execution devices using `device_list`.
- `/restart <device>`: restart one execution node using `device_restart`.
  If more than one device exists and no target is supplied, ask the user to choose; never guess.
  If the target reports active routed jobs, do not set `allow_active_jobs=true` without explicit user confirmation.
  After restart, verify the same device_id, fingerprint, and route_generation.
- `/restart gateway`: explain that an unresponsive gateway cannot restart itself through the same MCP endpoint; use the host/out-of-band restart path instead.

There is no `/deviceList` alias. Use `/devices`.

If a client does not expose a native slash-command picker but sends these strings as ordinary chat text, follow the same semantics.
"""


def package_files(
    *,
    version: str,
    target: str,
    gateway_url: str = "",
    app_id: str = "",
    plugin_name: str = PLUGIN_NAME,
    display_name: str = DISPLAY_NAME,
    command_dir: Path = COMMANDS_DIR,
) -> dict[str, str]:
    if target not in TARGETS:
        raise ValueError(f"unsupported target: {target}")
    validate_target_identity(plugin_name=plugin_name, target=target)

    description = (
        "RemoteMCP V2 connector with durable multi-device routed execution "
        "and operator commands."
    )
    interface = _interface(display_name)

    native_manifest = {
        "interface": interface,
        "name": plugin_name,
        "version": version,
        "description": description,
        "author": {"name": "RemoteMCP OSS"},
        "keywords": ["mcp", "remote", "multi-device", "self-hosted"],
        "skills": "./skills",
    }
    portable_openai = {"interface": interface}

    files = {
        "skills/remote-mcp/SKILL.md": _skill(),
    }

    if target == TARGET_DESKTOP_DIRECT_MCP:
        mcp_url = normalize_mcp_url(gateway_url)
        native_manifest["mcpServers"] = "./.mcp.json"
        files[".mcp.json"] = _json(
            {
                "mcpServers": {
                    "remote": {
                        "type": "streamable-http",
                        "url": mcp_url,
                        "headers": {},
                    }
                }
            }
        )
        files["mcp.json"] = _json(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                "mcpServers": {
                    "remote": {
                        "type": "streamable-http",
                        "url": mcp_url,
                    }
                },
            }
        )
    else:
        normalized_app_id = normalize_app_id(app_id)
        native_manifest["apps"] = "./.app.json"
        portable_openai["apps"] = "./.app.json"
        files[".app.json"] = _json(
            {
                "apps": {
                    "remote": {
                        "id": normalized_app_id,
                        "required": True,
                    }
                }
            }
        )

    files["plugin.json"] = _json(
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": plugin_name,
            "version": version,
            "description": description,
            "extensions": {"com.openai": portable_openai},
        }
    )
    files[".codex-plugin/plugin.json"] = _json(native_manifest)
    files.update(load_command_files(command_dir))
    return files


def build_archive(
    output: Path,
    *,
    target: str,
    gateway_url: str = "",
    app_id: str = "",
    plugin_name: str = PLUGIN_NAME,
    display_name: str = DISPLAY_NAME,
    version: str = DEFAULT_VERSION,
    command_dir: Path = COMMANDS_DIR,
) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = package_files(
        version=version,
        target=target,
        gateway_url=gateway_url,
        app_id=app_id,
        plugin_name=plugin_name,
        display_name=display_name,
        command_dir=command_dir,
    )
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, file_content in sorted(files.items()):
            zf.writestr(name, file_content)
    return output


def main() -> None:
    load_local_public_url()
    parser = argparse.ArgumentParser(
        description="Build a private ChatGPT plugin archive for RemoteMCP."
    )
    parser.add_argument(
        "--target",
        choices=TARGETS,
        required=True,
        help=(
            "desktop-direct-mcp embeds MCP manifests and is Desktop only; "
            "web-app-ref references an existing eligible ChatGPT app and embeds no MCP manifest."
        ),
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("PUBLIC_URL", ""),
        help="RemoteMCP public HTTPS origin for desktop-direct-mcp.",
    )
    parser.add_argument(
        "--app-id",
        default=os.environ.get("REMOTEMCP_CHATGPT_APP_ID", ""),
        help=(
            "Existing eligible app id for web-app-ref "
            "(asdk_app_*, connector_*, or templated_apps_*)."
        ),
    )
    parser.add_argument(
        "--plugin-name",
        default=PLUGIN_NAME,
        help="Plugin identity name. Use a distinct name for a clean web sibling.",
    )
    parser.add_argument(
        "--display-name",
        default=DISPLAY_NAME,
        help="User-facing plugin display name.",
    )
    parser.add_argument(
        "--out",
        default="dist/remote-mcp-chatgpt-plugin.zip",
        help="Output ZIP path.",
    )
    parser.add_argument("--version", default=DEFAULT_VERSION, help="Plugin package version.")
    args = parser.parse_args()

    try:
        path = build_archive(
            Path(args.out),
            target=args.target,
            gateway_url=args.url,
            app_id=args.app_id,
            plugin_name=args.plugin_name,
            display_name=args.display_name,
            version=args.version,
        )
    except ValueError as exc:
        parser.error(str(exc))
        return
    print(path)


if __name__ == "__main__":
    main()
