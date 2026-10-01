from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


def test_gateway_recovery_scripts_are_out_of_band_and_state_preserving():
    configure=(ROOT/"Configure-RemoteMCP-Gateway.ps1").read_text(encoding="utf-8")
    start=(ROOT/"Start-RemoteMCP-Gateway.ps1").read_text(encoding="utf-8")
    restart=(ROOT/"Restart-RemoteMCP-Gateway.ps1").read_text(encoding="utf-8")
    watchdog=(ROOT/"Watch-RemoteMCP-Gateway.ps1").read_text(encoding="utf-8")
    status=(ROOT/"Test-RemoteMCP-Gateway.ps1").read_text(encoding="utf-8")

    assert "ConvertFrom-SecureString" in configure
    assert "gateway-owner-password.txt" in configure
    assert "DPAPI-encrypted" in configure
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


def test_gateway_recovery_does_not_pair_or_revoke_devices():
    combined="\n".join(
        (ROOT/name).read_text(encoding="utf-8").lower()
        for name in (
            "Configure-RemoteMCP-Gateway.ps1",
            "Start-RemoteMCP-Gateway.ps1",
            "Restart-RemoteMCP-Gateway.ps1",
            "Watch-RemoteMCP-Gateway.ps1",
            "Test-RemoteMCP-Gateway.ps1",
        )
    )
    assert "device_pair_begin" not in combined
    assert "device_revoke" not in combined
    assert "remotemcp.node pair" not in combined
