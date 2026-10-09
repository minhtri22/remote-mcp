"""Read-only, fail-closed snapshot adjudication before a Windows node cutover.

This module MUST NOT stop processes, change node identity, edit runtime databases,
poll commands, or authorize a scientific job. The snapshot is only a diagnostic
artifact. An independent on-host preflight and separately approved operator
cutover remain mandatory.
"""
from __future__ import annotations

import argparse
import json
import ntpath
from pathlib import Path


PROTECTED_PIDS = (9152, 24528)
SIX_TASKS = (
    ("tsk_4b7fc9641a6da674cbae1bca", "rjob_106ba7160b813a11201e4017f50b9c11"),
    ("tsk_e883dfd5c2e536399e836e7d", "rjob_9b4dc61402e220e24739f2143732fee9"),
)
MAX_HEARTBEAT_AGE_MS = 90_000


def _winpath(path: object) -> str:
    return ntpath.normcase(ntpath.normpath(str(path or "").replace("/", "\\")))


def assess_upgrade_snapshot(snapshot: dict) -> dict:
    """Make gate assertions without mutating anything or accessing the node.

    The collected snapshot is not itself authenticated. A PASS here is an
    *evidence-readiness* verdict only, NEVER deployment permission.
    """
    blockers: list[str] = []
    findings: list[str] = []
    if not isinstance(snapshot, dict) or snapshot.get("schema") != "remotemcp.six-iris-safe-upgrade.v1":
        return {
            "status": "HOLD", "blockers": ["SNAPSHOT_SCHEMA_INVALID"],
            "cutover_authorized": False, "science_rerun_authorized": False,
            "mutation_performed": False,
        }

    expected = snapshot.get("expected") or {}
    gateway = snapshot.get("gateway") or {}
    node = snapshot.get("node") or {}
    os_capture = snapshot.get("os_capture") or {}
    release = snapshot.get("target_release") or {}
    node_identity = snapshot.get("node_identity") or {}
    moment = snapshot.get("snapshot_at_ms")
    age = None
    try:
        age = int(moment) - int(gateway["last_seen_at_ms"])
        if age < 0 or age > MAX_HEARTBEAT_AGE_MS:
            blockers.append("SIGNED_GATEWAY_HEARTBEAT_STALE_OR_CLOCK_MISMATCH")
    except (ValueError, KeyError, TypeError):
        blockers.append("SIGNED_GATEWAY_HEARTBEAT_MISSING")
    if gateway.get("state") != "ONLINE" or gateway.get("capacity_signal_fresh") is not True:
        blockers.append("GATEWAY_NOT_FRESH_ONLINE")
    for key in ("device_id", "key_fingerprint_sha256", "route_generation"):
        want = expected.get(key)
        observed = (gateway.get(key), node_identity.get(key))
        if want in (None, "") or any(str(v) != str(want) for v in observed):
            blockers.append(f"NODE_IDENTITY_MISMATCH_{key.upper()}")
    if _winpath(expected.get("execution_root")) != _winpath(node.get("execution_root")):
        blockers.append("LIVE_NODE_EXECUTION_ROOT_MISMATCH")
    if _winpath(expected.get("runtime_dir")) != _winpath(node.get("runtime_dir")):
        blockers.append("LIVE_NODE_RUNTIME_DIR_MISMATCH")
    if not expected.get("execution_root") or not expected.get("runtime_dir"):
        blockers.append("FROZEN_PATH_IDENTITY_MISSING")
    if not node.get("release_commit") or str(node.get("release_commit")) != str(gateway.get("node_release_commit")):
        blockers.append("NODE_RELEASE_ATTESTATION_MISMATCH")
    if not release.get("sha256") or not release.get("source_dir"):
        blockers.append("PINNED_TARGET_RELEASE_MISSING")
    if release.get("staged_and_verified") is not True:
        blockers.append("TARGET_RELEASE_NOT_VERIFIED_ON_HOST")

    # Avoid claiming no active jobs from a gateway registry count. Only the
    # signed node capacity observation is authoritative, and only if fresh.
    if gateway.get("authoritative_active_node_jobs") != 0:
        blockers.append("ACTIVE_NODE_JOBS_UNRESOLVED")
    if gateway.get("capacity_reconciliation_complete") is not True:
        blockers.append("NODE_JOB_CAPACITY_UNRECONCILED")

    processes = os_capture.get("processes") or []
    proc_by_id = {}
    for p in processes:
        try:
            pid = int(p["pid"])
            if pid in proc_by_id:
                blockers.append("DUPLICATE_OS_PROCESS_ID")
            proc_by_id[pid] = p
        except (ValueError, TypeError, KeyError):
            blockers.append("MALFORMED_OS_PROCESS")
    node_pids = set()
    watchdog_pids = set()
    for pid, p in proc_by_id.items():
        cmd = str(p.get("command_line") or "").lower()
        if "remotemcp.node run" in cmd or "-m remotemcp.node" in cmd and " run " in cmd:
            node_pids.add(pid)
        if "watch-remotemcp-node.ps1" in cmd:
            watchdog_pids.add(pid)
    if not node_pids:
        blockers.append("NODE_PROCESS_ABSENT")
    roots = []
    for pid in node_pids:
        try:
            parent = int(proc_by_id[pid].get("parent_pid") or 0)
        except ValueError:
            parent = 0
        if parent not in node_pids:
            roots.append(pid)
    if len(roots) != 1:
        blockers.append("NODE_LOGICAL_ROOT_COUNT_NOT_ONE")
    # An orphaned inner Python process must not masquerade as a healthy node
    # root after its parent disappears. Exact PID and creation timestamp are
    # pinned by the operator before the cutover. Never trust PID alone.
    try:
        pinned_node_root = int(expected["node_root_pid"])
    except (KeyError, ValueError, TypeError):
        pinned_node_root = None
        blockers.append("PINNED_NODE_ROOT_IDENTITY_MISSING")
    if pinned_node_root is not None:
        if roots != [pinned_node_root]:
            blockers.append("NODE_ROOT_PID_CHANGED_OR_ORPHANED")
        actual_node = proc_by_id.get(pinned_node_root) or {}
        if (not expected.get("node_root_created_at")
            or str(actual_node.get("created_at")) != str(expected["node_root_created_at"])):
            blockers.append("NODE_ROOT_CREATION_CHANGED_OR_UNVERIFIED")
    try:
        pinned_watchdog = int(expected["watchdog_pid"])
    except (KeyError, ValueError, TypeError):
        pinned_watchdog = None
        blockers.append("PINNED_WATCHDOG_IDENTITY_MISSING")
    if pinned_watchdog is not None:
        if pinned_watchdog not in watchdog_pids:
            blockers.append("WATCHDOG_PID_CHANGED_OR_ABSENT")
        actual_watchdog = proc_by_id.get(pinned_watchdog) or {}
        if (not expected.get("watchdog_created_at")
            or str(actual_watchdog.get("created_at")) != str(expected["watchdog_created_at"])):
            blockers.append("WATCHDOG_CREATION_CHANGED_OR_UNVERIFIED")
    for pid in node_pids:
        cmd = str(proc_by_id[pid].get("command_line") or "")
        if _winpath(expected.get("runtime_dir")) not in _winpath(cmd):
            # Node argv contains the runtime path, and need not contain --root.
            blockers.append("NODE_RUNTIME_ARGUMENT_MISMATCH")
            break

    for pid in PROTECTED_PIDS:
        p = proc_by_id.get(pid)
        if not p:
            blockers.append(f"PROTECTED_PROCESS_{pid}_MISSING")
            continue
        if "ru0_c_u3_executor.py" not in str(p.get("command_line") or "").lower():
            blockers.append(f"PROTECTED_PROCESS_{pid}_IDENTITY_MISMATCH")
        frozen_creation = (expected.get("protected_created_at") or {}).get(str(pid))
        if not frozen_creation or str(p.get("created_at")) != str(frozen_creation):
            blockers.append(f"PROTECTED_PROCESS_{pid}_CREATION_MISMATCH")
        chain = set()
        current = pid
        while current:
            if current in chain:
                blockers.append(f"PROTECTED_PROCESS_{pid}_ANCESTRY_CYCLE")
                break
            chain.add(current)
            if current in node_pids:
                blockers.append(f"PROTECTED_PROCESS_{pid}_CHILD_OF_NODE")
                break
            row = proc_by_id.get(current)
            if row is None:
                # A former ancestor may have exited. Do not infer cutover
                # safety from an incomplete parent chain.
                blockers.append(f"PROTECTED_PROCESS_{pid}_ANCESTRY_INCOMPLETE")
                break
            try:
                parent = int(row.get("parent_pid") or 0)
            except ValueError:
                blockers.append(f"PROTECTED_PROCESS_{pid}_PARENT_INVALID")
                break
            current = parent

    if not watchdog_pids:
        blockers.append("EXISTING_WATCHDOG_NOT_IDENTIFIED")
    target_dir = _winpath(release.get("source_dir"))
    for pid in watchdog_pids:
        cmd = _winpath(proc_by_id[pid].get("command_line"))
        if not target_dir or target_dir not in cmd:
            blockers.append("WATCHDOG_PINNED_TO_DIFFERENT_RELEASE")
            break

    # Every queued/possibly delivered job needs an exact command journal
    # adjudication; merely seeing the proxy QUEUED is insufficient.
    trace = snapshot.get("six_command_evidence") or []
    by_proxy = {x.get("proxy_job_id"): x for x in trace if isinstance(x, dict)}
    for task_id, proxy_id in SIX_TASKS:
        x = by_proxy.get(proxy_id)
        if not x or x.get("task_id") != task_id:
            blockers.append(f"SIX_COMMAND_PROOF_MISSING_{proxy_id}")
            continue
        if x.get("node_job_id") is None:
            if x.get("exact_command_state") not in ("QUEUED", "CANCELLED", "LEASED", "IN_DOUBT"):
                blockers.append(f"SIX_COMMAND_STATE_UNRESOLVED_{proxy_id}")
            # Still cannot deploy/replay blindly: the node may have received
            # a command whose gateway reply was lost.
            if x.get("node_journal_attested") is not True:
                blockers.append(f"SIX_NODE_JOURNAL_NOT_ATTESTED_{proxy_id}")
            if x.get("delivered_attempts") is None:
                blockers.append(f"SIX_DELIVERY_ATTEMPT_UNKNOWN_{proxy_id}")
        else:
            if x.get("node_journal_attested") is not True:
                blockers.append(f"SIX_EXISTING_JOB_PROVENANCE_UNVERIFIED_{proxy_id}")
    if not blockers:
        findings.append("STATIC_EVIDENCE_CONSISTENT")
    return {
        "status": "EVIDENCE_PREFLIGHT_PASS" if not blockers else "HOLD",
        "blockers": sorted(set(blockers)),
        "findings": findings,
        "heartbeat_age_ms": age,
        "node_process_ids": sorted(node_pids),
        "node_logical_roots": sorted(roots),
        "watchdog_process_ids": sorted(watchdog_pids),
        "protected_pids": list(PROTECTED_PIDS),
        # This module is ONLY an assessor. All live cutovers need a separate
        # explicit operator-reviewed authorization even if checks pass.
        "cutover_authorized": False,
        "science_rerun_authorized": False,
        "mutation_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only SIX/IRIS node upgrade gate")
    parser.add_argument("snapshot", type=Path, help="Local evidence snapshot JSON")
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    report = assess_upgrade_snapshot(snapshot)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "EVIDENCE_PREFLIGHT_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
