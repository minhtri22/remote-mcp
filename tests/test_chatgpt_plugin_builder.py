from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_builder():
    path = ROOT / "scripts" / "build_chatgpt_plugin.py"
    spec = importlib.util.spec_from_file_location("build_chatgpt_plugin", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_normalize_mcp_url():
    b = _load_builder()
    assert b.normalize_mcp_url("https://example.com") == "https://example.com/mcp"
    assert b.normalize_mcp_url("https://example.com/mcp") == "https://example.com/mcp"


def test_normalize_app_id():
    b = _load_builder()
    assert b.normalize_app_id("asdk_app_example") == "asdk_app_example"
    assert b.normalize_app_id("plugin_asdk_app_example") == "asdk_app_example"
    assert b.normalize_app_id("connector_example") == "connector_example"
    assert b.normalize_app_id("templated_apps_example") == "templated_apps_example"

    with pytest.raises(ValueError):
        b.normalize_app_id("plugins_example")

    with pytest.raises(ValueError):
        b.normalize_app_id("https://example.com/mcp")


def test_commands_are_first_class_sources():
    expected = {"status.md", "devices.md", "restart.md"}
    actual = {
        p.name
        for p in (ROOT / "commands").glob("*.md")
        if not p.name.startswith("_")
    }
    assert actual == expected

    conventions = (ROOT / "commands" / "_conventions.md").read_text(encoding="utf-8")
    assert "/status" in conventions
    assert "/devices" in conventions
    assert "/restart <device>" in conventions
    assert "/deviceList" in conventions  # documented only as a forbidden alias

    builder = (ROOT / "scripts" / "build_chatgpt_plugin.py").read_text(encoding="utf-8")
    assert 'COMMANDS_DIR = REPO_ROOT / "commands"' in builder
    assert "load_command_files" in builder
    assert "# RemoteMCP Status" not in builder
    assert "# RemoteMCP Devices" not in builder
    assert "# Restart RemoteMCP Execution Node" not in builder


def test_desktop_direct_mcp_archive_preserves_current_behavior(tmp_path):
    b = _load_builder()
    out = b.build_archive(
        tmp_path / "plugin.zip",
        target=b.TARGET_DESKTOP_DIRECT_MCP,
        gateway_url="https://example.com",
        version="1.2.3",
    )

    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert {
            ".codex-plugin/plugin.json",
            ".mcp.json",
            "mcp.json",
            "plugin.json",
            "skills/remote-mcp/SKILL.md",
            "commands/status.md",
            "commands/devices.md",
            "commands/restart.md",
        } <= names
        assert ".app.json" not in names
        assert "commands/_conventions.md" not in names

        native = json.loads(z.read(".codex-plugin/plugin.json"))
        assert native["name"] == "remote-mcp-v2bd"
        assert native["mcpServers"] == "./.mcp.json"
        assert "apps" not in native

        mcp = json.loads(z.read(".mcp.json"))
        assert mcp["mcpServers"]["remote"]["url"] == "https://example.com/mcp"

        plugin = json.loads(z.read("plugin.json"))
        assert plugin["version"] == "1.2.3"
        assert plugin["name"] == "remote-mcp-v2bd"
        assert "mcpServers" not in plugin["extensions"]["com.openai"]
        assert "apps" not in plugin["extensions"]["com.openai"]

        skill = z.read("skills/remote-mcp/SKILL.md").decode()
        assert "project_register_on_device" in skill
        assert "gateway-local compatibility tools" in skill
        assert "/status" in skill
        assert "/devices" in skill
        assert "/restart <device>" in skill
        assert "There is no `/deviceList` alias" in skill
        assert "private.example.invalid" not in skill

        for name in ("status.md", "devices.md", "restart.md"):
            source = (ROOT / "commands" / name).read_text(encoding="utf-8")
            packaged = z.read(f"commands/{name}").decode()
            assert packaged == source


def test_web_app_ref_archive_has_no_direct_mcp_declaration(tmp_path):
    b = _load_builder()
    out = b.build_archive(
        tmp_path / "web-plugin.zip",
        target=b.TARGET_WEB_APP_REF,
        app_id="plugin_asdk_app_example",
        version="1.3.0",
    )

    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())

        assert ".app.json" in names
        assert ".mcp.json" not in names
        assert "mcp.json" not in names

        native = json.loads(z.read(".codex-plugin/plugin.json"))
        assert native["name"] == "remote-mcp-v2bd"
        assert native["apps"] == "./.app.json"
        assert "mcpServers" not in native

        portable = json.loads(z.read("plugin.json"))
        openai_ext = portable["extensions"]["com.openai"]
        assert openai_ext["apps"] == "./.app.json"
        assert "mcpServers" not in openai_ext

        app = json.loads(z.read(".app.json"))
        assert app == {
            "apps": {
                "remote": {
                    "id": "asdk_app_example",
                    "required": True,
                }
            }
        }


def test_web_app_ref_fails_closed_without_eligible_app_id(tmp_path):
    b = _load_builder()

    with pytest.raises(ValueError):
        b.build_archive(
            tmp_path / "web-plugin.zip",
            target=b.TARGET_WEB_APP_REF,
            app_id="",
            version="1.3.0",
        )

    with pytest.raises(ValueError):
        b.build_archive(
            tmp_path / "web-plugin.zip",
            target=b.TARGET_WEB_APP_REF,
            app_id="plugins_not_an_app",
            version="1.3.0",
        )


def test_targets_do_not_silently_cross_wire(tmp_path):
    b = _load_builder()

    desktop = b.package_files(
        version="1.3.0",
        target=b.TARGET_DESKTOP_DIRECT_MCP,
        gateway_url="https://example.com",
    )
    assert ".mcp.json" in desktop
    assert "mcp.json" in desktop
    assert ".app.json" not in desktop

    web = b.package_files(
        version="1.3.0",
        target=b.TARGET_WEB_APP_REF,
        app_id="asdk_app_example",
    )
    assert ".app.json" in web
    assert ".mcp.json" not in web
    assert "mcp.json" not in web


def test_devices_command_is_read_only_and_canonical():
    text = (ROOT / "commands" / "devices.md").read_text(encoding="utf-8")
    assert "device_list" in text
    assert "read-only" in text.lower()
    assert "active routed job count" in text
    assert "Do not create or document a `/deviceList` alias" in text
    assert "/devices" in text


def test_all_user_facing_commands_have_required_sections():
    for path in sorted((ROOT / "commands").glob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "\ndescription:" in text
        for heading in (
            "## Preflight",
            "## Plan",
            "## Commands",
            "## Verification",
            "## Summary",
        ):
            assert heading in text, f"{path.name} missing {heading}"
