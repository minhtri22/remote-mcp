from __future__ import annotations
from enum import StrEnum

class NodeCommandState(StrEnum):
    RECEIVED="RECEIVED"
    EXECUTING="EXECUTING"
    SUCCEEDED="SUCCEEDED"
    FAILED="FAILED"
    IN_DOUBT="IN_DOUBT"

TERMINAL_NODE_COMMAND_STATES={
    NodeCommandState.SUCCEEDED.value,
    NodeCommandState.FAILED.value,
    NodeCommandState.IN_DOUBT.value,
}
