from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RoutingConfig:
    runtime_dir: Path
    public_origin: str
    pairing_key_path: Path
    pairing_ttl_seconds: int = 600
    heartbeat_seconds: int = 15
    offline_after_seconds: int = 60
    physical_process_max_age_seconds: int = 45
    poll_long_wait_seconds: int = 25
    command_lease_seconds: int = 45
    mutation_ttl_seconds: int = 120
    gateway_wait_seconds: int = 55
    nonce_retention_seconds: int = 600
    signed_timestamp_window_seconds: int = 60
    max_pair_body_bytes: int = 65536
    max_signed_body_bytes: int = 1048576

    @classmethod
    def from_env(cls, runtime_dir: Path, public_origin: str) -> "RoutingConfig":
        runtime = runtime_dir.resolve()
        return cls(
            runtime_dir=runtime,
            public_origin=public_origin.rstrip("/"),
            pairing_key_path=runtime/"device-pairing.key",
        )
