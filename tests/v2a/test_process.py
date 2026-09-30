from __future__ import annotations

import os

from remotemcp.durable.config import DEFAULT_DURABLE_ALLOWED_CMDS, safe_child_env
from remotemcp.durable.models import ProcessFingerprint
from remotemcp.durable.process import (
    command_sha256,
    fingerprint_process,
    verify_fingerprint,
    windows_process_options,
)


def test_current_process_fingerprint_and_pid_reuse_rejection():
    fp=fingerprint_process(os.getpid(),command_sha256(["self"]))
    assert fp is not None
    assert verify_fingerprint(fp)
    bad=ProcessFingerprint(
        pid=fp.pid,
        start_token=str(int(fp.start_token)+1) if fp.start_token.isdigit() else fp.start_token+"x",
        executable_canonical=fp.executable_canonical,
        command_sha256=fp.command_sha256,
    )
    assert verify_fingerprint(bad) is False


def test_safe_environment_and_default_project_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("OWNER_PASSWORD","should-not-leak")
    env=safe_child_env(tmp_path)
    assert env["HOME"]==str(tmp_path.resolve())
    assert "OWNER_PASSWORD" not in env
    for name in ("ollama","llama-cli","llama-server","llama-bench","cmake","ctest","ninja","uv","ffmpeg"):
        assert name in DEFAULT_DURABLE_ALLOWED_CMDS
    for shell in ("powershell","cmd","bash"):
        assert shell not in DEFAULT_DURABLE_ALLOWED_CMDS

def test_atomic_metadata_replace_retries_transient_permission_error(tmp_path, monkeypatch):
    import remotemcp.durable.process as procmod
    target=tmp_path/"worker.json"
    real_replace=procmod.os.replace
    calls={"n":0}

    def flaky(src,dst):
        calls["n"]+=1
        if calls["n"]<3:
            raise PermissionError(5,"transient")
        return real_replace(src,dst)

    monkeypatch.setattr(procmod.os,"replace",flaky)
    procmod.atomic_write_json(target,{"ok":True})
    assert calls["n"]==3
    assert target.read_text(encoding="utf-8")=='{"ok":true}'

def test_windows_process_options_hide_console_by_default(monkeypatch):
    if os.name!="nt":
        return
    import subprocess
    monkeypatch.delenv("REMOTEMCP_SHOW_CONSOLE",raising=False)
    flags, startupinfo = windows_process_options()
    assert flags & getattr(subprocess,"CREATE_NO_WINDOW",0x08000000)
    assert flags & getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0x00000200)
    assert not (flags & getattr(subprocess,"DETACHED_PROCESS",0x00000008))
    assert startupinfo is not None
    assert startupinfo.dwFlags & getattr(subprocess,"STARTF_USESHOWWINDOW",0x00000001)
    assert startupinfo.wShowWindow == getattr(subprocess,"SW_HIDE",0)
