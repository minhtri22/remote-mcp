from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from enum import StrEnum


class OperationState(StrEnum):
    RESERVED = "RESERVED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    IN_DOUBT = "IN_DOUBT"


class JobState(StrEnum):
    QUEUED = "QUEUED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    LOST = "LOST"


TERMINAL_JOB_STATES = {
    JobState.SUCCEEDED.value,
    JobState.FAILED.value,
    JobState.CANCELLED.value,
    JobState.LOST.value,
}


@dataclass(frozen=True)
class ProcessFingerprint:
    pid: int
    start_token: str
    executable_canonical: str
    command_sha256: str

    def to_dict(self) -> dict:
        return asdict(self)


def now_ms() -> int:
    return time.time_ns() // 1_000_000
