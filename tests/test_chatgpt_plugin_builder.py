from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_builder():
    path=ROOT/"scripts"/"build_chatgpt_plugin.py"
    spec=importlib.util.spec_from_file_location("build_chatgpt_plugin",path)
    module=importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_normalize_mcp_url():
    b=_load_builder()
    assert b.normalize_mcp_url("https://example.com")=="https://example.com/mcp"
    assert b.normalize_mcp_url("https://example.com/mcp")=="https://example.com/mcp"


def test_commands_are_first_class_sources():
    expected={"status.md","devices.md","restart.md"}
    actual={p.name for p in (ROOT/"commands").glob("*.md") if not p.name.startswith("_")}
    assert actual==expected

    conventions=(ROOT/"commands"/"_conventions.md").read_text(encoding="utf-8")
    assert "/status" in conventions
    assert "/devices" in conventions
    assert "/restart <device>" in conventions
    assert "/deviceList" in conventions  # documented only as a forbidden alias

    builder=(ROOT/"scripts"/"build_chatgpt_plugin.py").read_text(encoding="utf-8")
    assert 'COMMANDS_DIR = REPO_ROOT / "commands"' in builder
    assert "load_command_files" in builder
    assert '# RemoteMCP Status' not in builder
    assert '# RemoteMCP Devices' not in builder
    assert '# Restart RemoteMCP Execution Node' not in builder


def test_build_archive_uses_self_hosted_url_and_exact_command_sources(tmp_path):
    b=_load_builder()
    out=b.build_archive("https://example.com",tmp_path/"plugin.zip","1.2.3")
    with zipfile.ZipFile(out) as z:
        names=set(z.namelist())
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
        assert "commands/_conventions.md" not in names

        mcp=json.loads(z.read(".mcp.json"))
        assert mcp["mcpServers"]["remote"]["url"]=="https://example.com/mcp"

        plugin=json.loads(z.read("plugin.json"))
        assert plugin["version"]=="1.2.3"

        skill=z.read("skills/remote-mcp/SKILL.md").decode()
        assert "project_register_on_device" in skill
        assert "gateway-local compatibility tools" in skill
        assert "/status" in skill
        assert "/devices" in skill
        assert "/restart <device>" in skill
        assert "There is no `/deviceList` alias" in skill
        assert "remote.threadon.xyz" not in skill

        for name in ("status.md","devices.md","restart.md"):
            source=(ROOT/"commands"/name).read_text(encoding="utf-8")
            packaged=z.read(f"commands/{name}").decode()
            assert packaged==source


def test_devices_command_is_read_only_and_canonical():
    text=(ROOT/"commands"/"devices.md").read_text(encoding="utf-8")
    assert "device_list" in text
    assert "read-only" in text.lower()
    assert "active routed job count" in text
    assert "Do not create or document a `/deviceList` alias" in text
    assert "/devices" in text


def test_all_user_facing_commands_have_required_sections():
    for path in sorted((ROOT/"commands").glob("*.md")):
        if path.name.startswith("_"):
            continue
        text=path.read_text(encoding="utf-8")
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
