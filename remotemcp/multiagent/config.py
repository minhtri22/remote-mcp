from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from remotemcp.durable.config import DurableConfig


@dataclass(frozen=True)
class MultiAgentConfig:
    workspace_root: Path
    runtime_dir: Path
    heartbeat_interval_seconds: int = 30
    task_lease_ttl_seconds: int = 120
    max_project_active_tasks_default: int = 4
    poll_ms: int = 500

    @classmethod
    def from_durable(cls, durable: DurableConfig) -> "MultiAgentConfig":
        return cls(
            workspace_root=durable.workspace_root.resolve(),
            runtime_dir=durable.runtime_dir.resolve(),
        )