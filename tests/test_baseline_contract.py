import asyncio
import os
from pathlib import Path

import pytest

import server


EXPECTED_TOOLS = {
    "list_dir",
    "read_file",
    "write_file",
    "edit_file",
    "search",
    "run_command",
}

EXPECTED_ALLOWED_CMDS = {
    "ls",
    "cat",
    "grep",
    "rg",
    "git",
    "python",
    "pip",
    "node",
    "npm",
    "pytest",
}


def test_frozen_runtime_constants():
    assert server.MAX_OUT == 20_000
    assert server.CMD_TIMEOUT == 60
    assert server.ALLOWED_CMDS == EXPECTED_ALLOWED_CMDS


def test_frozen_tool_surface_is_callable():
    for name in EXPECTED_TOOLS:
        assert hasattr(server, name), name
        assert callable(getattr(server, name)), name


def test_safe_accepts_workspace_paths_and_blocks_parent_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", tmp_path.resolve())

    inside = server.safe("a/b.txt")
    assert inside == (tmp_path / "a" / "b.txt").resolve()

    with pytest.raises(ValueError, match="ngoài workspace"):
        server.safe("../outside.txt")


def test_safe_blocks_symlink_escape_when_symlinks_are_available(tmp_path, monkeypatch):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")

    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this Windows environment")

    monkeypatch.setattr(server, "ROOT", root.resolve())
    with pytest.raises(ValueError, match="ngoài workspace"):
        server.safe("link/secret.txt")


def test_file_tool_baseline_semantics(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", tmp_path.resolve())

    result = server.write_file("notes/a.txt", "one\ntwo\nthree\n")
    assert "Đã ghi" in result

    assert server.read_file("notes/a.txt", offset=1, limit=1) == "two"

    listing = server.list_dir("notes")
    assert "[F] notes\\a.txt" in listing or "[F] notes/a.txt" in listing

    assert server.edit_file("notes/a.txt", "two", "TWO") == "OK"
    assert "TWO" in server.read_file("notes/a.txt")

    (tmp_path / "dup.txt").write_text("x x", encoding="utf-8")
    msg = server.edit_file("dup.txt", "x", "y")
    assert "xuất hiện 2 lần" in msg
    assert (tmp_path / "dup.txt").read_text(encoding="utf-8") == "x x"


def test_search_skips_dot_git_and_honors_hit_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", tmp_path.resolve())

    (tmp_path / "a.txt").write_text("needle one\nneedle two\n", encoding="utf-8")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "hidden.txt").write_text("needle hidden\n", encoding="utf-8")

    out = server.search("needle", ".", max_hits=1)
    assert "a.txt:1:" in out
    assert "hidden.txt" not in out
    assert len(out.splitlines()) == 1


def test_clip_baseline():
    assert server.clip("abc") == "abc"
    long_text = "x" * (server.MAX_OUT + 7)
    out = server.clip(long_text)
    assert out.startswith("x" * server.MAX_OUT)
    assert "cắt bớt 7 ký tự" in out


def test_run_command_rejects_non_allowlisted_command():
    out = asyncio.run(server.run_command("powershell Get-ChildItem"))
    assert "Từ chối" in out
    assert "powershell" in out


def test_run_command_passes_exact_baseline_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", tmp_path.resolve())

    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"ok\n", b""

        def kill(self):
            captured["killed"] = True

    async def fake_create_subprocess_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    out = asyncio.run(server.run_command('python -c "print(1)"'))

    assert out == "[exit 0]\nok\n"
    assert captured["argv"][0] == "python"
    assert captured["kwargs"]["cwd"] == tmp_path.resolve()
    assert captured["kwargs"]["env"] == {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path.resolve()),
    }


def test_run_command_timeout_kills_child(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", tmp_path.resolve())
    monkeypatch.setattr(server, "CMD_TIMEOUT", 0.001)

    captured = {"killed": False}

    class FakeProcess:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(1)
            return b"", b""

        def kill(self):
            captured["killed"] = True

    async def fake_create_subprocess_exec(*argv, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    out = asyncio.run(server.run_command('python -c "print(1)"'))
    assert out == "Timeout sau 0.001s"
    assert captured["killed"] is True
