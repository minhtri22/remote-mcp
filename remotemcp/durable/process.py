from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from .config import executable_key, safe_child_env
from .errors import DurableError
from .models import ProcessFingerprint


def command_sha256(argv: list[str]) -> str:
    data = json.dumps(argv, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A fixed .tmp name is unsafe on Windows when a concurrent reader briefly
    # holds the destination without delete sharing. Use a unique temp file and
    # bounded retry around the atomic replace.
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        last_error = None
        for _ in range(50):
            try:
                os.replace(tmp, path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.02)
        if last_error is not None:
            raise last_error
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_write_json(path: Path, value: dict) -> bytes:
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    atomic_write_bytes(path, data)
    return data


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def resolve_executable(argv0: str, cwd: Path, allowed: frozenset[str]) -> str:
    key = executable_key(argv0)
    if key not in allowed:
        raise DurableError(
            "COMMAND_NOT_ALLOWED",
            f"executable '{key}' is not in durable allowlist",
            executable=argv0,
        )

    has_path = any(sep in argv0 for sep in ("/", "\\")) or Path(argv0).is_absolute()
    if has_path:
        p = Path(argv0)
        if not p.is_absolute():
            p = cwd / p
        p = p.resolve()
        if not p.is_file():
            raise DurableError(
                "SPAWN_INVALID_EXECUTABLE",
                "executable path does not exist",
                executable=str(p),
            )
        return str(p)

    found = shutil.which(argv0)
    if not found and os.name == "nt":
        for suffix in (".exe", ".cmd", ".bat", ".com"):
            found = shutil.which(argv0 + suffix)
            if found:
                break
    if not found:
        raise DurableError(
            "SPAWN_INVALID_EXECUTABLE",
            "executable was not found on PATH",
            executable=argv0,
        )
    return str(Path(found).resolve())


def _windows_fingerprint(pid: int, command_hash: str) -> ProcessFingerprint | None:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    class FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    OpenProcess = kernel32.OpenProcess
    OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    OpenProcess.restype = wintypes.HANDLE

    CloseHandle = kernel32.CloseHandle
    CloseHandle.argtypes = [wintypes.HANDLE]
    CloseHandle.restype = wintypes.BOOL

    GetProcessTimes = kernel32.GetProcessTimes
    GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
    ]
    GetProcessTimes.restype = wintypes.BOOL

    QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
    QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    QueryFullProcessImageNameW.restype = wintypes.BOOL

    handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation = FILETIME()
        exit_time = FILETIME()
        kernel_time = FILETIME()
        user_time = FILETIME()
        if not GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        start = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)

        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            executable = ""
        else:
            executable = str(Path(buf.value).resolve())

        return ProcessFingerprint(
            pid=pid,
            start_token=str(start),
            executable_canonical=executable,
            command_sha256=command_hash,
        )
    finally:
        CloseHandle(handle)


def _posix_fingerprint(pid: int, command_hash: str) -> ProcessFingerprint | None:
    proc = Path("/proc") / str(pid)
    try:
        stat = (proc / "stat").read_text(encoding="utf-8")
        # Field 22 is process starttime. comm may contain spaces, so split after ') '.
        tail = stat.rsplit(") ", 1)[1].split()
        start = tail[19]
        executable = str((proc / "exe").resolve())
        return ProcessFingerprint(
            pid=pid,
            start_token=start,
            executable_canonical=executable,
            command_sha256=command_hash,
        )
    except (FileNotFoundError, PermissionError, OSError, IndexError):
        return None


def fingerprint_process(pid: int, command_hash: str) -> ProcessFingerprint | None:
    if os.name == "nt":
        return _windows_fingerprint(pid, command_hash)
    return _posix_fingerprint(pid, command_hash)


def verify_fingerprint(expected: ProcessFingerprint) -> bool:
    current = fingerprint_process(expected.pid, expected.command_sha256)
    if current is None:
        return False
    if current.pid != expected.pid or current.start_token != expected.start_token:
        return False
    if (
        expected.executable_canonical
        and current.executable_canonical
        and os.path.normcase(current.executable_canonical)
        != os.path.normcase(expected.executable_canonical)
    ):
        return False
    return True


def signal_owned_process(expected: ProcessFingerprint) -> bool:
    if not verify_fingerprint(expected):
        raise DurableError(
            "PROCESS_OWNERSHIP_MISMATCH",
            "process fingerprint no longer matches",
            pid=expected.pid,
        )
    try:
        os.kill(expected.pid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False


def windows_show_console() -> bool:
    value = os.environ.get("REMOTEMCP_SHOW_CONSOLE", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def windows_process_options(
    *,
    show_console: bool | None = None,
    new_process_group: bool = True,
) -> tuple[int, subprocess.STARTUPINFO | None]:
    """Return Windows subprocess options that never create/show a console by default.

    Debugging is opt-in with REMOTEMCP_SHOW_CONSOLE=1.  CREATE_NO_WINDOW is
    used instead of DETACHED_PROCESS so console executables such as Python,
    Ollama, llama.cpp and test runners stay invisible without changing their
    stdout/stderr pipe/file semantics.
    """
    if os.name != "nt":
        return 0, None

    visible = windows_show_console() if show_console is None else bool(show_console)
    flags = 0
    if new_process_group:
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

    startupinfo = None
    if not visible:
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)

    return flags, startupinfo


def current_console_window_handle() -> int:
    """Best-effort diagnostic used by qualification tests on Windows."""
    if os.name != "nt":
        return 0
    try:
        return int(ctypes.windll.kernel32.GetConsoleWindow() or 0)
    except Exception:
        return -1


def worker_argv(runtime_dir: Path, job_id: str, launch_nonce: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "remotemcp.durable.worker",
        "--runtime-dir",
        str(runtime_dir.resolve()),
        "--job-id",
        job_id,
        "--launch-nonce",
        launch_nonce,
    ]


def spawn_worker(
    runtime_dir: Path,
    job_id: str,
    launch_nonce: str,
    workspace_root: Path,
    stderr_path: Path,
) -> tuple[subprocess.Popen, list[str]]:
    argv = worker_argv(runtime_dir, job_id, launch_nonce)
    env = safe_child_env(workspace_root)
    # The worker imports the local package from the same source tree.
    package_root = str(Path(__file__).resolve().parents[2])
    current_pp = os.environ.get("PYTHONPATH", "")
    env["PYTHONPATH"] = package_root + (os.pathsep + current_pp if current_pp else "")
    env["PYTHONIOENCODING"] = "utf-8"

    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(stderr_path, "ab", buffering=0)
    creationflags, startupinfo = windows_process_options()
    env["REMOTEMCP_SHOW_CONSOLE"] = "1" if windows_show_console() else "0"
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(workspace_root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            shell=False,
            creationflags=creationflags,
            startupinfo=startupinfo,
            close_fds=True,
        )
    finally:
        log.close()
    return proc, argv
