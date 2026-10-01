from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path


def _load_builder():
    path=Path(__file__).resolve().parents[1]/"scripts"/"build_chatgpt_plugin.py"
    spec=importlib.util.spec_from_file_location("build_chatgpt_plugin",path)
    module=importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_normalize_mcp_url():
    b=_load_builder()
    assert b.normalize_mcp_url("https://example.com")=="https://example.com/mcp"
    assert b.normalize_mcp_url("https://example.com/mcp")=="https://example.com/mcp"


def test_build_archive_uses_self_hosted_url(tmp_path):
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
        } <= names
        mcp=json.loads(z.read(".mcp.json"))
        assert mcp["mcpServers"]["remote"]["url"]=="https://example.com/mcp"
        plugin=json.loads(z.read("plugin.json"))
        assert plugin["version"]=="1.2.3"
        skill=z.read("skills/remote-mcp/SKILL.md").decode()
        assert "project_register_on_device" in skill
        assert "gateway-local compatibility tools" in skill
        assert "remote.threadon.xyz" not in skill
