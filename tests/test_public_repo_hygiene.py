from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]
TEXT_SUFFIXES={".md",".py",".json",".ps1",".txt",".sql",".yml",".yaml",".toml",".cfg",".ini"}
# SHA-256 of the private deployment hostname that must never appear in public source.
PRIVATE_HOST_SHA256="f4d00e83d7def28db4a3bf4e78734473402104c2ee6b5735cacd38ec8830b355"


def tracked_files() -> list[Path]:
    out=subprocess.check_output(["git","-C",str(ROOT),"ls-files","-z"])
    return [ROOT/p.decode("utf-8") for p in out.split(b"\0") if p]


def text_files() -> list[Path]:
    return [
        p for p in tracked_files()
        if p.name==".gitignore" or p.suffix.lower() in TEXT_SUFFIXES
    ]


def test_private_deployment_hostname_not_in_public_source():
    fqdn=re.compile(r"\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
    hits=[]
    for path in text_files():
        text=path.read_text(encoding="utf-8",errors="ignore")
        for token in fqdn.findall(text):
            digest=hashlib.sha256(token.lower().encode("utf-8")).hexdigest()
            if digest==PRIVATE_HOST_SHA256:
                hits.append(str(path.relative_to(ROOT)))
    assert not hits, f"private deployment hostname leaked in: {sorted(set(hits))}"


def test_public_operational_records_do_not_contain_real_machine_or_runtime_ids():
    paths=[
        ROOT/"README.md",
        *sorted((ROOT/"docs").rglob("*")),
        *sorted((ROOT/"specs").rglob("*")),
        *sorted((ROOT/"scripts").rglob("*")),
    ]
    patterns={
        "physical Windows hostname": re.compile(r"\bDESKTOP-[A-Z0-9-]{4,}\b"),
        "device id": re.compile(r"\bdev_[0-9a-f]{32}\b"),
        "agent id": re.compile(r"\bagt_[0-9a-f]{32}\b"),
        "session id": re.compile(r"\bses_[0-9a-f]{32}\b"),
        "owner id": re.compile(r"\bown_[0-9a-f]{32}\b"),
        "user profile path": re.compile(r"\bC:\\Users\\[A-Za-z0-9._-]+",re.I),
    }
    hits=[]
    for path in paths:
        if not path.is_file() or (path.suffix.lower() not in TEXT_SUFFIXES and path.name!="README.md"):
            continue
        text=path.read_text(encoding="utf-8",errors="ignore")
        for name,rx in patterns.items():
            if rx.search(text):
                hits.append(f"{path.relative_to(ROOT)}: {name}")
    assert not hits, "deployment-specific identifiers leaked:\n" + "\n".join(hits)


def test_no_committed_local_env_or_private_key_material():
    rels={str(p.relative_to(ROOT)).replace("\\","/") for p in tracked_files()}
    assert ".env" not in rels
    assert not any(p.startswith(".env.") and p!=".env.example" for p in rels)

    private_key=re.compile(
        r"-----BEGIN (?:PRIVATE|OPENSSH PRIVATE|EC PRIVATE|RSA PRIVATE) KEY-----"
    )
    hits=[]
    for path in text_files():
        if private_key.search(path.read_text(encoding="utf-8",errors="ignore")):
            hits.append(str(path.relative_to(ROOT)))
    assert not hits, f"private key material leaked in: {hits}"


def test_env_ignore_policy():
    text=(ROOT/".gitignore").read_text(encoding="utf-8")
    assert ".env" in text
    assert ".env.*" in text
    assert "!.env.example" in text


def test_public_text_does_not_contain_personal_account_or_email_markers():
    patterns={
        "named connected account": re.compile(r"\b[A-Za-z0-9._-]+['’]s RemoteDesktop account\b",re.I),
        "non-example email": re.compile(
            r"\b[A-Z0-9._%+-]+@(?!example\.com\b)[A-Z0-9.-]+\.[A-Z]{2,}\b",
            re.I,
        ),
    }
    hits=[]
    for path in text_files():
        text=path.read_text(encoding="utf-8",errors="ignore")
        for name,rx in patterns.items():
            if rx.search(text):
                hits.append(f"{path.relative_to(ROOT)}: {name}")
    assert not hits, "personal account metadata leaked:\n" + "\n".join(hits)


def test_env_example_is_placeholder_only():
    text=(ROOT/".env.example").read_text(encoding="utf-8")
    required=(
        "PUBLIC_URL=https://mcp.example.com",
        "PORT=<GATEWAY_PORT>",
        "MCP_ROOT=<WORKSPACE_ROOT>",
        "MCP_STATE=<PRIVATE_STATE_FILE>",
        "MCP_RUNTIME_DIR=<PRIVATE_RUNTIME_DIR>",
        "REMOTEMCP_CHATGPT_APP_ID=<CHATGPT_APP_ID>",
    )
    for item in required:
        assert item in text
