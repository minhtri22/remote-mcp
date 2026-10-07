from __future__ import annotations

import asyncio
import os

import server


def test_legacy_run_command_preserves_output_and_safe_env(tmp_path, monkeypatch):
    monkeypatch.setattr(server,"ROOT",tmp_path.resolve())
    captured={}

    class Fake:
        returncode=7
        async def communicate(self):
            return b"hello\n",b""
        def kill(self):
            captured["killed"]=True

    async def fake_exec(*argv,**kwargs):
        captured["argv"]=argv; captured["kwargs"]=kwargs
        return Fake()

    monkeypatch.setattr(server.asyncio,"create_subprocess_exec",fake_exec)
    out=asyncio.run(server.run_command('python -c "print(1)"'))
    assert out=="[exit 7]\nhello\n"
    assert captured["kwargs"]["cwd"]==tmp_path.resolve()
    env=captured["kwargs"]["env"]
    assert env["HOME"]==str(tmp_path.resolve())
    assert env["PATH"]==os.environ["PATH"]
    assert "OWNER_PASSWORD" not in env
    assert captured["kwargs"]["creationflags"] == server.windows_process_options()[0]
    if os.name == "nt":
        assert captured["kwargs"]["startupinfo"] is not None
    else:
        assert captured["kwargs"]["startupinfo"] is None


def test_legacy_run_command_rejects_shell():
    out=asyncio.run(server.run_command("powershell Get-ChildItem"))
    assert out.startswith("Từ chối:")
