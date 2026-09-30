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
    "COMSPEC", "PATHEXT", "SYSTEMDRIVE",
)


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
    env["HOME"] = str(workspace_root.resolve())
    return env
