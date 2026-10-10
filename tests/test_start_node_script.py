from pathlib import Path


def test_root_start_script_contract():
    p = Path(__file__).resolve().parents[1] / "Start-RemoteMCP-Node.ps1"
    text = p.read_text(encoding="utf-8")
    assert "RemoteMCP\\node-venv" in text
    assert "active-runtime.txt" in text
    assert "device.json" in text
    assert "device-ed25519.pem" in text
    assert "No paired RemoteMCP runtime identity was found" in text
    assert "This starter never creates a new device identity" in text
    assert "httpx==0.28.1" in text
    assert "cryptography==46.0.6" in text
    assert "remotemcp.node" in text
    assert "--runtime-dir" in text
    assert "--root" in text
    assert "[string]$RootDir" in text
    assert "REMOTEMCP_NODE_ROOT" in text
    assert "IsPathFullyQualified" not in text
    assert "IsPathRooted" in text
    assert "OS_DRIVE_RESEARCH_ROOT_FORBIDDEN" in text
    assert "NODE_ROOT_MISMATCH_RESTART_REQUIRED" in text
    assert "[switch]$Restart" in text
    assert " pair " not in text.lower()


def test_starter_archives_old_node_logs_before_redirecting_new_process():
    p = Path(__file__).resolve().parents[1] / "Start-RemoteMCP-Node.ps1"
    text = p.read_text(encoding="utf-8")
    assert 'node-error.log' in text
    assert 'node.log' in text
    assert 'Move-Item -LiteralPath $oldLog -Destination $archive -ErrorAction Stop' in text
    assert '$info.Length -gt 0' in text
    assert text.index('Move-Item -LiteralPath $oldLog') < text.index('Start-Process -FilePath $NodePython')
