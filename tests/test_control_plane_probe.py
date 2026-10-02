from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_control_plane_probe_is_read_only_and_classifies_layers():
    text = (ROOT / "Test-RemoteMCP-ControlPlane.ps1").read_text(encoding="utf-8")

    assert "127.0.0.1:$port/.well-known/oauth-authorization-server" in text
    assert '$publicUrl/.well-known/oauth-authorization-server' in text

    for label in (
        "LOCAL_GATEWAY_8099",
        "PUBLIC_TUNNEL_OR_EDGE",
        "CHATGPT_CONNECTOR_PATH",
        "LOCAL_AND_PUBLIC_HEALTHY",
    ):
        assert label in text

    for forbidden in (
        "Stop-Process",
        "Start-Process",
        "device_revoke",
        "device_pair",
        "project_register",
        "task_create",
        "task_job_submit",
    ):
        assert forbidden not in text

    assert "mutation_free = $true" in text
