from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_windows_job_memory_diagnostic_is_read_only_and_zero_temp():
    text = (ROOT / "scripts" / "diagnose_windows_job_memory_limits.ps1").read_text(encoding="utf-8")
    assert "IsProcessInJob" in text
    assert "QueryInformationJobObject" in text
    assert "Win32_NamedJobObjectProcess" in text
    assert "Win32_NamedJobObjectLimit" in text
    assert "PROCESS_MEMORY" in text
    assert "JOB_MEMORY" in text
    assert "temp_artifacts_created = $false" in text

    forbidden = (
        "Stop-Process", "Start-Process", "SetInformationJobObject",
        "AssignProcessToJobObject", "CreateJobObject", "Remove-Item",
        "New-Item", "Set-Content", "Add-Content", "Out-File",
        "task_job_submit", "device_revoke", "device_pair"
    )
    for token in forbidden:
        assert token not in text

def test_diagnostic_preserves_unnamed_job_uncertainty():
    text = (ROOT / "scripts" / "diagnose_windows_job_memory_limits.ps1").read_text(encoding="utf-8")
    assert "JOB_MEMBERSHIP_CONFIRMED_BUT_UNNAMED_LIMITS_UNRESOLVED" in text
    assert "unnamed-job limits remain unresolved" in text
