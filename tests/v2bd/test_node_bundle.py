from __future__ import annotations

import io
import zipfile

from remotemcp.routing.join_script import render_windows_join_script
from remotemcp.routing.node_bundle import build_node_bundle


def test_default_join_source_is_gateway_bundle():
    script=render_windows_join_script(
        public_origin="https://owner.example",
        device_name="node",
        pairing_bundle="pair_x|pc1_y",
    )
    assert "$SourceUrl = 'https://owner.example/device/v1/node-bundle.zip'" in script
    assert "github.com" not in script


def test_node_bundle_contains_required_runtime_files():
    data=build_node_bundle()
    assert data.startswith(b"PK")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names=set(z.namelist())
    assert "remotemcp/node/__main__.py" in names
    assert "remotemcp/node/service.py" in names
    assert "remotemcp/node/client.py" in names
    assert "remotemcp/routing/crypto.py" in names
    assert "remotemcp/durable/errors.py" in names
