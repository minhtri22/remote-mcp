"""Zero-science adversarial checks for the SIX/IRIS read-only cutover gate."""
from __future__ import annotations

from copy import deepcopy

from scripts.six_iris_safe_node_upgrade_gate import assess_upgrade_snapshot


DEVICE = "dev_dd73ebfa742f468f2d212bade88c175b"
FP = "b657d5395e393e0957a9ed358bb5a1fe1588fde5d3a44be71295a68d1e73def9"
RUNTIME = r"C:\Users\minht\AppData\Local\RemoteMCP\runtime"
ROOT = r"D:\WORK\RESEARCH"
TARGET = r"D:\2.RemoteMCP-releases\safe-target"


def fixture():
    return {
        "schema": "remotemcp.six-iris-safe-upgrade.v1",
        "snapshot_at_ms": 1000000,
        "expected": {
            "device_id": DEVICE, "key_fingerprint_sha256": FP,
            "route_generation": 1, "execution_root": ROOT,
            "runtime_dir": RUNTIME,
        },
        "gateway": {
            "state": "ONLINE", "last_seen_at_ms": 995000,
            "device_id": DEVICE, "key_fingerprint_sha256": FP,
            "route_generation": 1, "node_release_commit": "old-sha",
            "capacity_signal_fresh": True,
            "capacity_reconciliation_complete": True,
            "authoritative_active_node_jobs": 0,
        },
        "node_identity": {
            "device_id": DEVICE, "key_fingerprint_sha256": FP,
            "route_generation": 1,
        },
        "node": {
            "execution_root": ROOT, "runtime_dir": RUNTIME,
            "release_commit": "old-sha",
        },
        "target_release": {
            "sha256": "f" * 64, "source_dir": TARGET,
            "staged_and_verified": True,
        },
        "os_capture": {
            "processes": [
                {"pid": 1, "parent_pid": 0, "created_at": "2026-01-01",
                 "command_line": "system"},
                {"pid": 24528, "parent_pid": 1, "created_at": "2026-10-06T07:38:15",
                 "command_line": r"D:\WORK\RESEARCH\2.CQG-RU0G\ru0_c_u3_executor.py"},
                {"pid": 9152, "parent_pid": 24528, "created_at": "2026-10-06T07:38:15",
                 "command_line": r"D:\WORK\RESEARCH\2.CQG-RU0G\ru0_c_u3_executor.py"},
                {"pid": 400, "parent_pid": 1, "created_at": "2026-10-09",
                 "command_line": f'python.exe -m remotemcp.node run --runtime-dir "{RUNTIME}"'},
                {"pid": 401, "parent_pid": 400, "created_at": "2026-10-09",
                 "command_line": f'python.exe -m remotemcp.node run --runtime-dir "{RUNTIME}"'},
                {"pid": 500, "parent_pid": 1, "created_at": "2026-10-09",
                 "command_line": f'powershell.exe -File "{TARGET}\\Watch-RemoteMCP-Node.ps1"'},
            ]
        },
        "six_command_evidence": [
            {
                "task_id": "tsk_4b7fc9641a6da674cbae1bca",
                "proxy_job_id": "rjob_106ba7160b813a11201e4017f50b9c11",
                "node_job_id": None, "exact_command_state": "CANCELLED",
                "delivered_attempts": 1, "node_journal_attested": True,
            },
            {
                "task_id": "tsk_e883dfd5c2e536399e836e7d",
                "proxy_job_id": "rjob_9b4dc61402e220e24739f2143732fee9",
                "node_job_id": None, "exact_command_state": "QUEUED",
                "delivered_attempts": 0, "node_journal_attested": True,
            },
        ],
    }


def test_consistent_evidence_is_only_preflight_never_cutover_authorization():
    report = assess_upgrade_snapshot(fixture())
    assert report["status"] == "EVIDENCE_PREFLIGHT_PASS"
    assert report["blockers"] == []
    assert report["node_logical_roots"] == [400]
    assert report["cutover_authorized"] is False
    assert report["science_rerun_authorized"] is False
    assert report["mutation_performed"] is False


def test_realistic_live_old_watchdog_and_missing_command_journal_fail_closed():
    s = fixture()
    s["os_capture"]["processes"][-1]["command_line"] = (
        r'powershell.exe -File "D:\2.RemoteMCP-releases\80fe818\Watch-RemoteMCP-Node.ps1"'
    )
    s["six_command_evidence"] = []
    s["os_capture"]["processes"] = [
        x for x in s["os_capture"]["processes"] if x["pid"] != 1
    ]
    report = assess_upgrade_snapshot(s)
    assert report["status"] == "HOLD"
    assert "WATCHDOG_PINNED_TO_DIFFERENT_RELEASE" in report["blockers"]
    assert any("SIX_COMMAND_PROOF_MISSING" in x for x in report["blockers"])
    assert "PROTECTED_PROCESS_9152_ANCESTRY_INCOMPLETE" in report["blockers"]
    assert report["cutover_authorized"] is False


def test_identity_replacement_never_passes():
    for key, replacement in [
        ("device_id", "dev_new_identity"),
        ("route_generation", 2),
        ("key_fingerprint_sha256", "0" * 64),
    ]:
        s = fixture()
        s["node_identity"][key] = replacement
        report = assess_upgrade_snapshot(s)
        assert report["status"] == "HOLD"
        assert any("NODE_IDENTITY_MISMATCH" in x for x in report["blockers"])


def test_incomplete_node_or_protected_chain_never_passes():
    for omitted_pid in [9152, 24528, 400, 500]:
        s = fixture()
        s["os_capture"]["processes"] = [
            p for p in s["os_capture"]["processes"] if p["pid"] != omitted_pid
        ]
        report = assess_upgrade_snapshot(s)
        assert report["status"] == "HOLD"
        assert report["cutover_authorized"] is False


def test_gateway_stale_and_unreconciled_jobs_block():
    for mutate in [
        lambda s: s["gateway"].update(last_seen_at_ms=0),
        lambda s: s["gateway"].update(authoritative_active_node_jobs=1),
        lambda s: s["gateway"].update(capacity_reconciliation_complete=False),
        lambda s: s["gateway"].update(capacity_signal_fresh=False),
        lambda s: s["node"].update(execution_root=r"C:\Users\minht\RemoteMCP-Workspace"),
        lambda s: s["target_release"].update(staged_and_verified=False),
        lambda s: s["six_command_evidence"][0].update(node_journal_attested=False),
        lambda s: s["six_command_evidence"][1].update(delivered_attempts=None),
    ]:
        s = fixture()
        mutate(s)
        assert assess_upgrade_snapshot(s)["status"] == "HOLD"


def test_duplicate_node_roots_are_rejected():
    s = fixture()
    s["os_capture"]["processes"].append({
        "pid": 600, "parent_pid": 1, "created_at": "2026-10-09",
        "command_line": f'python.exe -m remotemcp.node run --runtime-dir "{RUNTIME}"',
    })
    report = assess_upgrade_snapshot(s)
    assert "NODE_LOGICAL_ROOT_COUNT_NOT_ONE" in report["blockers"]


def test_protected_science_descendant_of_node_is_rejected():
    s = fixture()
    for row in s["os_capture"]["processes"]:
        if row["pid"] == 24528:
            row["parent_pid"] = 401
    report = assess_upgrade_snapshot(s)
    assert "PROTECTED_PROCESS_9152_CHILD_OF_NODE" in report["blockers"]
    assert "PROTECTED_PROCESS_24528_CHILD_OF_NODE" in report["blockers"]


def test_missing_schema_never_grants_any_authority():
    report = assess_upgrade_snapshot({"schema": "unknown"})
    assert report["status"] == "HOLD"
    assert report["mutation_performed"] is False
    assert report["science_rerun_authorized"] is False
