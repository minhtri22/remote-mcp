from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_node_watchdog_is_runtime_explicit_and_identity_preserving():
    text = (ROOT / "Watch-RemoteMCP-Node.ps1").read_text(encoding="utf-8")

    assert "[Parameter(Mandatory=$true)]" in text
    assert "[string]$RuntimeDir" in text
    assert "[string]$RootDir" in text
    assert "[string]$NodeSourceDir" in text
    assert "device.json" in text
    assert "device-ed25519.pem" in text
    assert "device_id" in text
    assert "key_fingerprint_sha256" in text
    assert "route_generation" in text
    assert "Assert-IdentityUnchanged" in text
    assert "Get-CimInstance Win32_Process" in text
    assert "--runtime-dir" in text
    assert "process absence threshold reached" in text
    assert "& $StartScript -RuntimeDir $RuntimeDir -RootDir $RootDir -SourceDir $NodeSourceDir" in text
    assert "OS_DRIVE_RESEARCH_ROOT_FORBIDDEN" in text
    assert "Get-LogicalNodeRoots" in text
    assert "multiple independent exact-runtime node roots detected" in text
    assert "raw_processes" in text

    # Supervisor must not kill a live process, pair a replacement identity, or
    # interpret gateway/connector health as a reason to restart the node.
    lower = text.lower()
    assert "stop-process" not in lower
    assert " -restart" not in lower
    assert "remotemcp.node pair" not in lower
    assert "device_pair_begin" not in lower
    assert "device_revoke" not in lower
    assert "invoke-webrequest" not in lower
    assert "device_list" not in lower
    assert "device_status" not in lower
    assert "active-runtime.txt" not in lower


def test_node_supervisor_installer_is_explicit_per_runtime():
    text = (ROOT / "Install-RemoteMCP-Node-Supervisor.ps1").read_text(encoding="utf-8")

    assert "[Parameter(Mandatory=$true)]" in text
    assert "[string]$RuntimeDir" in text
    assert "[string]$RootDir" in text
    assert "[string]$NodeSourceDir" in text
    assert "OS_DRIVE_RESEARCH_ROOT_FORBIDDEN" in text
    assert "-RootDir" in text
    assert "device.json" in text
    assert "device-ed25519.pem" in text
    assert "New-ScheduledTaskAction" in text
    assert "New-ScheduledTaskTrigger -AtLogOn" in text
    assert "New-ScheduledTaskPrincipal" in text
    assert "Register-ScheduledTask" in text
    assert "-RestartCount 3" in text
    assert "-MultipleInstances IgnoreNew" in text
    assert "RemoteMCP-Node-Supervisor-" in text
    assert "StartNow" in text
    assert "PlanOnly" in text
    assert "REMOTEMCP_NODE_SUPERVISOR_PLAN_ONLY=PASS" in text
    assert "REMOTEMCP_NODE_SUPERVISOR_INSTALL=PASS" in text
    assert "PersistenceMode" in text
    assert "Install-StartupPersistence" in text
    assert "Scheduled Task persistence unavailable; falling back to per-user Startup" in text
    assert "Register-ScheduledTask" in text
    assert "-ErrorAction Stop" in text
    assert "Get-LogicalNodeRoots" in text
    assert "Supervisor deployment requires exactly one logical node root" in text
    assert "Logical node process group:" in text

    lower = text.lower()
    assert "active-runtime.txt" not in lower
    assert "remotemcp.node pair" not in lower
    assert "device_pair_begin" not in lower
    assert "device_revoke" not in lower


def test_logical_node_root_algorithm_collapses_parent_child_launcher_chain():
    # Mirrors the PowerShell helper contract with synthetic process topology.
    processes = [
        {"ProcessId": 100, "ParentProcessId": 50},
        {"ProcessId": 101, "ParentProcessId": 100},
    ]
    ids = {p["ProcessId"] for p in processes}
    roots = [p for p in processes if p["ParentProcessId"] not in ids]
    assert [p["ProcessId"] for p in roots] == [100]


def test_logical_node_root_algorithm_detects_independent_duplicates():
    processes = [
        {"ProcessId": 100, "ParentProcessId": 50},
        {"ProcessId": 101, "ParentProcessId": 100},
        {"ProcessId": 200, "ParentProcessId": 60},
        {"ProcessId": 201, "ParentProcessId": 200},
    ]
    ids = {p["ProcessId"] for p in processes}
    roots = [p for p in processes if p["ParentProcessId"] not in ids]
    assert {p["ProcessId"] for p in roots} == {100, 200}
