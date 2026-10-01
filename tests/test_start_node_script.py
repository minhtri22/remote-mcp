from pathlib import Path


def test_root_start_script_contract():
    p = Path(__file__).resolve().parents[1] / "Start-RemoteMCP-Node.ps1"
    text = p.read_text(encoding="utf-8")
    assert "RemoteMCP\\runtime" in text
    assert "RemoteMCP\\node-venv" in text
    assert "httpx==0.28.1" in text
    assert "cryptography==46.0.6" in text
    assert "remotemcp.node" in text
    assert "--runtime-dir" in text
    assert "[switch]$Restart" in text
    assert " pair " not in text.lower()
