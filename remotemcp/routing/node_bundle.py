from __future__ import annotations

import io
import zipfile
from pathlib import Path


def build_node_bundle() -> bytes:
    """Return a deterministic-enough source bundle for the outbound node runtime.

    The bundle is served by the user's own gateway, so joining/updating a node
    does not depend on GitHub or any RemoteMCP-operated central service.
    """
    package_root = Path(__file__).resolve().parents[1]  # .../remotemcp
    release_root = package_root.parent
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for path in sorted(package_root.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            arc = path.relative_to(release_root).as_posix()
            info = zipfile.ZipInfo(arc)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, path.read_bytes())
    return buf.getvalue()
