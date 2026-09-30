from __future__ import annotations

from enum import StrEnum


class SessionState(StrEnum):
    ACTIVE="ACTIVE"
    STALE="STALE"
    CLOSED="CLOSED"


class TaskState(StrEnum):
    CREATED="CREATED"
    READY="READY"
    CLAIMED="CLAIMED"
    RUNNING="RUNNING"
    BLOCKED="BLOCKED"
    RECOVERABLE="RECOVERABLE"
    COMPLETED="COMPLETED"
    FAILED="FAILED"
    CANCELLED="CANCELLED"


TERMINAL_TASK_STATES={
    TaskState.COMPLETED.value,
    TaskState.FAILED.value,
    TaskState.CANCELLED.value,
}

ACTIVE_TASK_STATES=(TaskState.CLAIMED.value,TaskState.RUNNING.value)