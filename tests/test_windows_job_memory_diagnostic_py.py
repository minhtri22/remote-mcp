from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_python_windows_job_diagnostic_is_read_only_and_zero_temp():
    text = (ROOT / "scripts" / "diagnose_windows_job_memory_limits.py").read_text(encoding="utf-8")
    assert "IsProcessInJob" in text
    assert "QueryInformationJobObject" in text
    assert "Win32_NamedJobObjectProcess" in text
    assert "Win32_NamedJobObjectLimit" in text
    assert '"temp_artifacts_created": False' in text
    assert '"temp_cleanup_required": False' in text

    forbidden = (
        "SetInformationJobObject",
        "AssignProcessToJobObject",
        "CreateJobObject",
        "tempfile.",
        "mkdtemp",
        "NamedTemporaryFile",
        "shutil.rmtree",
        "os.remove(",
        "os.unlink(",
        "Path.write_text",
        "Path.write_bytes",
    )
    for token in forbidden:
        assert token not in text

def test_python_diagnostic_preserves_unnamed_job_uncertainty():
    text = (ROOT / "scripts" / "diagnose_windows_job_memory_limits.py").read_text(encoding="utf-8")
    assert "JOB_MEMBERSHIP_CONFIRMED_BUT_UNNAMED_LIMITS_UNRESOLVED" in text
    assert "unnamed-job limits" in text
