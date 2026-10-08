from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


def test_gateway_recovery_scripts_are_out_of_band_and_state_preserving():
    configure=(ROOT/"Configure-RemoteMCP-Gateway.ps1").read_text(encoding="utf-8")
    start=(ROOT/"Start-RemoteMCP-Gateway.ps1").read_text(encoding="utf-8")
    restart=(ROOT/"Restart-RemoteMCP-Gateway.ps1").read_text(encoding="utf-8")
    update=(ROOT/"Update-RemoteMCP-Gateway.ps1").read_text(encoding="utf-8")
    watchdog=(ROOT/"Watch-RemoteMCP-Gateway.ps1").read_text(encoding="utf-8")
    status=(ROOT/"Test-RemoteMCP-Gateway.ps1").read_text(encoding="utf-8")

    assert "ConvertFrom-SecureString" in configure
    assert "gateway-owner-password.txt" in configure
    assert "DPAPI-encrypted" in configure
    assert "WriteAllText($SecretFile" in configure
    assert "(Get-Content $SecretFile -Raw).Trim()" in start
    assert "gateway-venv" in configure
    assert '"mcp[cli]<2"' in configure
    assert '"httpx==0.28.1"' in configure
    assert '"cryptography==46.0.6"' in configure
    assert "Creating dedicated RemoteMCP gateway virtual environment" in configure
    assert "MCP_STATE" in start
    assert "MCP_RUNTIME_DIR" in start
    assert "PUBLIC_URL" in start
    assert "OWNER_PASSWORD" in start
    assert ".well-known/oauth-authorization-server" in start
    assert "Refusing to kill it" in start
    assert "Stop-Process" in start
    assert "server.py" in start
    assert "-Restart" in restart
    assert "FailureThreshold" in watchdog
    assert "restart threshold reached" in watchdog
    assert ".well-known/oauth-authorization-server" in status

    assert "Invoke-IsolatedGatewayReleaseProbe" in update
    assert "REMOTEMCP_GATEWAY_RELEASE_PROBE=PASS" in update
    assert "REMOTEMCP_GATEWAY_UPDATE_PREFLIGHT_ONLY=PASS" in update
    assert "diagnostic.invalid" in update
    assert "Get-FreeLoopbackPort" in update
    assert "Invoke-ProductionRuntimeMigrationProbe" in update
    assert "REMOTEMCP_GATEWAY_PRODUCTION_STATE_PROBE=PASS" in update
    assert "REMOTEMCP_PRODUCTION_RUNTIME_MIGRATION_COMPAT=PASS" in update
    assert "src.backup(dst)" in update
    assert "db.bootstrap(target_version=5)" in update
    assert "REMOTEMCP_LEGACY_ROUTED_JOB_INVENTORY=PASS" in update
    assert "REMOTEMCP_SCHEMA_V4_BACKUP_PROBE=PASS" in update
    assert "REMOTEMCP_SCHEMA_V5_BACKUP_PROBE=PASS" in update
    assert "candidate did not converge to exact schema ledger [1,2,3,4,5]" in update
    assert "legacy production row data changed during schema migration probe" in update
    assert "schema v5 routed_jobs observability columns are incomplete" in update
    assert "schema v5 project binding lifecycle columns are incomplete" in update
    assert "task_job_admissions" in update
    assert update.index("Invoke-ProductionRuntimeMigrationProbe") < update.index("if ($PreflightOnly)")
    assert update.index("if ($PreflightOnly)") < update.index("$BackupFile =")

    assert "gateway-upgrade-failed-" in update
    assert "Copy-Item -Force $LiveErrFile $SavedLiveErr" in update

    assert '$RollbackStarter = Join-Path $NewSource "Start-RemoteMCP-Gateway.ps1"' in update
    assert "& $RollbackStarter -Restart -HealthTimeoutSec $HealthTimeoutSec" in update
    assert "OldRestart" not in update


def test_gateway_recovery_does_not_pair_or_revoke_devices():
    combined="\n".join(
        (ROOT/name).read_text(encoding="utf-8").lower()
        for name in (
            "Configure-RemoteMCP-Gateway.ps1",
            "Start-RemoteMCP-Gateway.ps1",
            "Restart-RemoteMCP-Gateway.ps1",
            "Update-RemoteMCP-Gateway.ps1",
            "Watch-RemoteMCP-Gateway.ps1",
            "Test-RemoteMCP-Gateway.ps1",
        )
    )
    assert "device_pair_begin" not in combined
    assert "device_revoke" not in combined
    assert "remotemcp.node pair" not in combined
