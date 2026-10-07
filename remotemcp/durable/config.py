from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DURABLE_ALLOWED_CMDS = frozenset({
    "ls", "cat", "grep", "rg", "git", "python", "pip", "node", "npm", "pytest",
    "ollama",
    "llama", "llama-cli", "llama-server", "llama-bench", "llama-batched-bench",
    "llama-completion", "llama-fit-params", "llama-gemma3-cli", "llama-gguf-split",
    "llama-imatrix", "llama-llava-cli", "llama-minicpmv-cli", "llama-mtmd-cli",
    "llama-mtmd-debug", "llama-perplexity", "llama-quantize", "llama-qwen2vl-cli",
    "llama-results", "llama-tokenize", "llama-tts",
    "cmake", "ctest", "ninja", "uv", "ffmpeg",
})

SAFE_ENV_KEYS = (
    "PATH", "SystemRoot", "WINDIR", "USERPROFILE", "TEMP", "TMP",
    "LOCALAPPDATA", "APPDATA", "PROGRAMDATA",
    "COMSPEC", "PATHEXT", "SYSTEMDRIVE",
)

MANAGED_ENV_ALLOWLIST_VAR = "REMOTEMCP_MANAGED_ENV_ALLOWLIST"
DEFAULT_MANAGED_ENV_KEYS = frozenset({
    "NVIDIA_API_KEY",
})


def _valid_env_name(name: str) -> bool:
    return bool(name) and "=" not in name and "\x00" not in name


def _windows_registry_env_value(key: str) -> str | None:
    """Read a persistent Windows environment value without mutating process state.

    A long-lived service process does not automatically receive variables added
    after it started. Managed execution may therefore consult the user's or
    machine's persistent Windows environment for explicitly allowlisted keys.
    """
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None

    locations = (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    )
    for hive, path in locations:
        try:
            with winreg.OpenKey(hive, path) as handle:
                value, _kind = winreg.QueryValueEx(handle, key)
        except OSError:
            continue
        if isinstance(value, str):
            return value
    return None


def _effective_managed_env_value(key: str) -> str | None:
    value = os.environ.get(key)
    if value is not None:
        return value
    return _windows_registry_env_value(key)


def managed_env_keys() -> frozenset[str]:
    raw = os.environ.get(MANAGED_ENV_ALLOWLIST_VAR)
    if raw is None:
        raw = _windows_registry_env_value(MANAGED_ENV_ALLOWLIST_VAR)
    extra = {
        item.strip()
        for item in (raw or "").split(",")
        if _valid_env_name(item.strip())
    }
    return frozenset(DEFAULT_MANAGED_ENV_KEYS | extra)


def executable_key(value: str) -> str:
    name = Path(value.replace("\\", "/")).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name


@dataclass(frozen=True)
class DurableConfig:
    workspace_root: Path
    runtime_dir: Path
    allowed_cmds: frozenset[str]
    max_parallel_jobs: int = 2
    poll_ms: int = 500
    starting_grace_seconds: int = 30

    @classmethod
    def from_env(cls, workspace_root: Path) -> "DurableConfig":
        runtime_dir = Path(
            os.environ.get("MCP_RUNTIME_DIR", "~/.remotemcp")
        ).expanduser().resolve()
        extra = {
            x.strip().lower()
            for x in os.environ.get("MCP_DURABLE_ALLOWED_CMDS", "").split(",")
            if x.strip()
        }
        max_jobs = max(1, int(os.environ.get("MCP_MAX_PARALLEL_JOBS", "2")))
        return cls(
            workspace_root=workspace_root.resolve(),
            runtime_dir=runtime_dir,
            allowed_cmds=frozenset(DEFAULT_DURABLE_ALLOWED_CMDS | extra),
            max_parallel_jobs=max_jobs,
        )


def safe_child_env(workspace_root: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in SAFE_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value

    # Managed jobs remain fail-closed for arbitrary parent secrets. Only the
    # explicit managed allowlist is copied. NVIDIA_API_KEY is included by
    # default because RemoteMCP scientific/model workloads depend on it.
    raw_allowlist = os.environ.get(MANAGED_ENV_ALLOWLIST_VAR)
    if raw_allowlist is None:
        raw_allowlist = _windows_registry_env_value(MANAGED_ENV_ALLOWLIST_VAR)
    if raw_allowlist is not None:
        env[MANAGED_ENV_ALLOWLIST_VAR] = raw_allowlist

    for key in managed_env_keys():
        value = _effective_managed_env_value(key)
        if value is not None:
            env[key] = value

    env["HOME"] = str(workspace_root.resolve())
    return env
