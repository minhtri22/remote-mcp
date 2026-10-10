"""Opt-in V31 node phase measurements; no route, job or lease modifications.

Disabled unless REMOTEMCP_NODE_CADENCE_TRACE=1. Records only allowlisted
scalar telemetry, not command payloads, argv, headers, keys, tokens or results.
The trace is an append-only diagnostic file inside the pre-existing node
runtime directory. It is not installed on production by this branch.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


SCHEMA = "remotemcp.node-poll-cadence.v1"

PHASES = frozenset({
    "capacity_duration_ms",
    "process_safety_duration_ms",
    "heartbeat_duration_ms",
    "poll_http_duration_ms",
    "execute_duration_ms",
    "result_ack_duration_ms",
})

ALLOWED_KEYS = frozenset({
    "schema", "cycle_seq", "device_id", "route_generation",
    "node_process_epoch", "utc_ms", "heartbeat_due",
    "capacity_duration_ms", "process_safety_duration_ms",
    "heartbeat_duration_ms", "poll_http_duration_ms",
    "execute_duration_ms", "result_ack_duration_ms",
    "poll_gap_monotonic_ms", "poll_status", "command_id",
    "reconnect_backoff_ms", "exception_phase", "exception_class",
    "sqlite_error_code", "transport_error_code", "trace_write_failed",
})


def _ms_since(start_ns: int) -> float:
    return round(max(0, time.perf_counter_ns() - start_ns) / 1_000_000, 3)


def sqlite_error_class(exc: Exception) -> str | None:
    """Keep Python/SQLite error classes distinct; never hide a locking failure."""
    if not isinstance(exc, sqlite3.Error):
        return None
    name = getattr(exc, "sqlite_errorname", None)
    message = str(exc).lower()
    if "locking protocol" in message:
        return "SQLITE_LOCKING_PROTOCOL"
    if name in {"SQLITE_BUSY", "SQLITE_LOCKED"}:
        return name
    if "database is locked" in message:
        return "SQLITE_DATABASE_LOCKED"
    if "disk i/o" in message:
        return "SQLITE_IOERR"
    return "SQLITE_OTHER"


class NodeCadence:
    def __init__(self, runtime_dir: Path, *, enabled: bool | None = None):
        self.enabled = (
            os.environ.get("REMOTEMCP_NODE_CADENCE_TRACE") == "1"
            if enabled is None else bool(enabled)
        )
        self._runtime = Path(runtime_dir)
        self._seq = 0
        self._last_poll_start_ns: int | None = None
        self._epoch = f"{os.getpid()}:{time.time_ns() // 1_000_000}"
        self.last_error_class: str | None = None

    def cycle(self, device: dict | None) -> dict | None:
        if not self.enabled:
            return None
        self._seq += 1
        device = device or {}
        return {
            "schema": SCHEMA,
            "cycle_seq": self._seq,
            "device_id": str(device.get("device_id") or ""),
            "route_generation": int(device.get("route_generation") or 0),
            "node_process_epoch": self._epoch,
            "utc_ms": time.time_ns() // 1_000_000,
            "heartbeat_due": False,
        }

    @contextmanager
    def phase(self, record: dict | None, field: str):
        if record is None:
            yield
            return
        if field not in PHASES:
            raise ValueError("CADENCE_PHASE_NOT_ALLOWLISTED")
        started = time.perf_counter_ns()
        if field == "poll_http_duration_ms":
            if self._last_poll_start_ns is not None:
                record["poll_gap_monotonic_ms"] = round(
                    max(0, started - self._last_poll_start_ns) / 1_000_000, 3
                )
            self._last_poll_start_ns = started
        try:
            yield
        except BaseException as exc:
            record["exception_phase"] = field.removesuffix("_duration_ms")
            record["exception_class"] = type(exc).__name__
            code = sqlite_error_class(exc)
            if code is not None:
                record["sqlite_error_code"] = code
            raise
        finally:
            record[field] = _ms_since(started)

    @staticmethod
    def transport_exception(record: dict | None, exc: Exception) -> None:
        if record is None:
            return
        record["exception_class"] = type(exc).__name__
        code = sqlite_error_class(exc)
        if code is not None:
            record["sqlite_error_code"] = code
        # Error strings can carry tokens or URLs: record class and stable code
        # only; do not emit error descriptions or full HTTP response bodies.
        from remotemcp.durable.errors import DurableError
        if isinstance(exc, DurableError):
            record["transport_error_code"] = str(exc.code)[:64]

    def emit(self, record: dict | None) -> None:
        if record is None:
            return
        data = {
            k: v for k, v in record.items()
            if k in ALLOWED_KEYS and isinstance(v, (str, bool, int, float, type(None)))
        }
        # Telemetry must never change the execution or retry state machine.
        try:
            path = self._runtime / "cadence" / "node-poll-cadence-v1.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
        except (OSError, ValueError, TypeError) as exc:
            self.last_error_class = type(exc).__name__
