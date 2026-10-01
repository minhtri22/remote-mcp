from remotemcp.routing.join_script import render_windows_join_script


def test_windows_join_script_is_single_file_and_instance_bound():
    script=render_windows_join_script(
        public_origin="https://example-owner.invalid",
        device_name="office-node",
        pairing_bundle="pair_abc|pc1_secret",
        source_url="https://example.invalid/source.zip",
    )
    assert "$GatewayUrl = 'https://example-owner.invalid'" in script
    assert "$DeviceName = 'office-node'" in script
    assert "$PairingBundle = 'pair_abc|pc1_secret'" in script
    assert "$SourceUrl = 'https://example.invalid/source.zip'" in script
    assert "--code-file $PairFile" in script
    assert "--pairing-id" not in script
    assert "httpx==0.28.1" in script
    assert "cryptography==46.0.6" in script
    assert "RemoteMCP-Node.vbs" in script
    assert "active-runtime.txt" in script
    assert "-WindowStyle Hidden" in script
    assert "remote.threadon.xyz" not in script


def test_windows_join_script_escapes_powershell_single_quotes():
    script=render_windows_join_script(
        public_origin="https://example.invalid",
        device_name="owner's node",
        pairing_bundle="pair_x|pc1_y",
    )
    assert "$DeviceName = 'owner''s node'" in script
