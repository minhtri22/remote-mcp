"""Windows-only qualification for headless RemoteMCP child processes.

Verifies:
- direct console child has no console/top-level visible window;
- foreground window is not stolen;
- V2-A durable worker/payload path is also headless;
- long-running payload remains observable through durable logs/status.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

if os.name != "nt":
    raise SystemExit("WINDOWS_ONLY_QUALIFICATION")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from remotemcp.durable.config import DurableConfig, DEFAULT_DURABLE_ALLOWED_CMDS
from remotemcp.durable.process import background_process_creationflags
from remotemcp.durable.service import DurableService


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GetForegroundWindow = user32.GetForegroundWindow
GetForegroundWindow.restype = wintypes.HWND

IsWindowVisible = user32.IsWindowVisible
IsWindowVisible.argtypes = [wintypes.HWND]
IsWindowVisible.restype = wintypes.BOOL

GetWindowThreadProcessId = user32.GetWindowThreadProcessId
GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
GetWindowThreadProcessId.restype = wintypes.DWORD

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
EnumWindows = user32.EnumWindows
EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
EnumWindows.restype = wintypes.BOOL


def visible_windows_for_pid(pid: int) -> list[int]:
    found: list[int] = []

    @EnumWindowsProc
    def callback(hwnd, _lparam):
        owner = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if int(owner.value) == int(pid) and IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    EnumWindows(callback, 0)
    return found


def direct_child_probe() -> dict:
    flags = background_process_creationflags()
    assert flags & 0x08000000, "CREATE_NO_WINDOW missing"
    foreground_before = int(GetForegroundWindow() or 0)
    code = (
        "import ctypes,time;"
        "print('CONSOLE='+str(int(ctypes.windll.kernel32.GetConsoleWindow() or 0)),flush=True);"
        "time.sleep(2)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=flags,
        close_fds=True,
    )
    try:
        time.sleep(0.35)
        foreground_after = int(GetForegroundWindow() or 0)
        windows = visible_windows_for_pid(proc.pid)
        line = proc.stdout.readline().strip() if proc.stdout else ""
        assert line == "CONSOLE=0", line
        assert windows == [], windows
        assert foreground_after == foreground_before, {
            "before": foreground_before,
            "after": foreground_after,
        }
        proc.wait(timeout=5)
        assert proc.returncode == 0
        return {
            "pid": proc.pid,
            "console_handle": 0,
            "visible_windows": 0,
            "foreground_unchanged": True,
        }
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


async def durable_probe() -> dict:
    with tempfile.TemporaryDirectory(prefix="rmcp-headless-") as td:
        base = Path(td)
        workspace = base / "workspace"
        runtime = base / "runtime"
        workspace.mkdir()
        cfg = DurableConfig(
            workspace_root=workspace,
            runtime_dir=runtime,
            allowed_cmds=DEFAULT_DURABLE_ALLOWED_CMDS,
            max_parallel_jobs=1,
            poll_ms=25,
            starting_grace_seconds=5,
        )
        service = DurableService(cfg)
        code = (
            "import ctypes,time;"
            "print('CONSOLE='+str(int(ctypes.windll.kernel32.GetConsoleWindow() or 0)),flush=True);"
            "print('LONG_PROCESS_STARTED',flush=True);"
            "time.sleep(2);"
            "print('LONG_PROCESS_DONE',flush=True)"
        )
        foreground_before = int(GetForegroundWindow() or 0)
        await service.start()
        try:
            submitted = await service.job_submit(
                "windows-headless-durable-probe",
                [sys.executable, "-c", code],
            )
            job_id = submitted["job_id"]
            payload_pid = None
            for _ in range(200):
                row = service.job_get(job_id)
                meta_path = runtime / "jobs" / job_id / "worker.json"
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        fp = meta.get("payload_fingerprint") or {}
                        if fp.get("pid"):
                            payload_pid = int(fp["pid"])
                    except Exception:
                        pass
                if row["state"] == "RUNNING" and payload_pid:
                    break
                await asyncio.sleep(0.025)
            assert payload_pid is not None, "payload pid not observed"
            time.sleep(0.25)
            foreground_after = int(GetForegroundWindow() or 0)
            windows = visible_windows_for_pid(payload_pid)
            assert windows == [], windows
            assert foreground_after == foreground_before, {
                "before": foreground_before,
                "after": foreground_after,
            }

            final = None
            for _ in range(240):
                final = service.job_get(job_id)
                if final["state"] in {"SUCCEEDED", "FAILED", "CANCELLED", "LOST"}:
                    break
                await asyncio.sleep(0.025)
            assert final and final["state"] == "SUCCEEDED", final
            logs = service.job_logs(job_id, "stdout", 0, 65536)["data"]
            assert "CONSOLE=0" in logs, logs
            assert "LONG_PROCESS_STARTED" in logs and "LONG_PROCESS_DONE" in logs, logs
            return {
                "job_id": job_id,
                "payload_pid": payload_pid,
                "console_handle": 0,
                "visible_windows": 0,
                "foreground_unchanged": True,
                "terminal": "SUCCEEDED",
            }
        finally:
            await service.stop()


async def main():
    result = {
        "verdict": "PASS",
        "direct_child": direct_child_probe(),
        "durable_worker_payload": await durable_probe(),
        "v2bd_node_jobs": "COVERED_BY_SHARED_V2A_DURABLE_LAUNCHER",
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
