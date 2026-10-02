from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_hung_gateway_capture_is_read_only():
    text = (ROOT / "Capture-RemoteMCP-Gateway-Diagnostics.ps1").read_text(encoding="utf-8")

    assert "Get-NetTCPConnection" in text
    assert "Get-Process" in text
    assert "Get-CimInstance Win32_Process" in text
    assert "gateway.log" in text
    assert "gateway-error.log" in text
    assert "cpu_delta_seconds" in text
    assert "state_counts" in text
    assert "mutation_free = $true" in text

    for forbidden in (
        "Stop-Process",
        "Start-Process",
        "Restart-Service",
        "Stop-Service",
        "Set-Content",
        "Add-Content",
        "Out-File",
        "device_pair",
        "device_revoke",
        "task_job_submit",
    ):
        assert forbidden not in text
