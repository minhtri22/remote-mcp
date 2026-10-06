from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def read(name):
    return (ROOT/name).read_text(encoding="utf-8-sig")

def test_zero_c_starter_has_explicit_non_os_infrastructure_contract():
    s=read("Start-RemoteMCP-Node.ps1")
    for token in (
        "[switch]$ZeroC",
        "[string]$VenvDir",
        "[string]$LogDir",
        "[string]$TempDir",
        "[string]$CacheDir",
        "[string]$ControlDir",
        "OS_DRIVE_REMOTEMCP_INFRA_FORBIDDEN",
        "REMOTEMCP_ZERO_C",
        "PIP_CACHE_DIR",
        "PYTHONPYCACHEPREFIX",
        "HF_HOME",
        "TORCH_HOME",
    ):
        assert token in s
    assert 'Join-Path $LogDir "node.log"' in s
    assert 'Join-Path $LogDir "node-error.log"' in s

def test_zero_c_watchdog_replays_exact_paths_without_localappdata_logs():
    s=read("Watch-RemoteMCP-Node.ps1")
    for token in (
        "[switch]$ZeroC",
        "VenvDir = $VenvDir",
        "LogDir = $LogDir",
        "TempDir = $TempDir",
        "CacheDir = $CacheDir",
        "ControlDir = $ControlDir",
        "ZeroC = $true",
        '$WatchdogLog = Join-Path $LogDir',
    ):
        assert token in s

def test_zero_c_supervisor_never_uses_startup_folder_fallback():
    s=read("Install-RemoteMCP-Node-Supervisor.ps1")
    assert '"RegistryRun"' in s
    assert "Install-RegistryRunPersistence" in s
    assert "ZERO_C_PERSISTENCE_UNAVAILABLE" in s
    assert '($PersistenceMode -eq "Auto" -and $ZeroC)' in s

def test_zero_c_migration_preserves_identity_and_removes_old_node_artifacts():
    s=read("Migrate-RemoteMCP-Node-ZeroC.ps1")
    for token in (
        "ExpectedDeviceId",
        "ExpectedFingerprint",
        "ExpectedRouteGeneration",
        "ACTIVE_NODE_JOBS_PRESENT",
        "OLD_RUNTIME_NODE_STILL_RUNNING",
        "ZERO_C_NODE_START_FAILED",
        "ZERO_C_SUPERVISOR_INSTALL_FAILED",
        "REMOTEMCP_MACHINE1_ZERO_C_MIGRATION_PASS",
        "old_runtime_removed",
    ):
        assert token in s

def test_v31_node_attestation_exposes_zero_c_paths():
    s=read("remotemcp/node/service.py")
    for key in (
        '"python_prefix"',
        '"infrastructure_root"',
        '"log_dir"',
        '"temp_dir"',
        '"cache_dir"',
        '"control_dir"',
        '"zero_c_mode"',
    ):
        assert key in s
