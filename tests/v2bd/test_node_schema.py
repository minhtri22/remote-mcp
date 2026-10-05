from __future__ import annotations

from pathlib import Path

import pytest

from remotemcp.durable.errors import DurableError
from remotemcp.node.db import NodeDatabase


def test_node_schema_bootstrap_repeat_and_byte_identity(tmp_path):
    db=NodeDatabase(tmp_path/"rt");db.bootstrap();db.bootstrap()
    assert db.get_meta("schema")["version"]==1
    root=Path(__file__).resolve().parents[2]
    assert (root/"specs"/"v2bd_node_schema_v1.sql").read_bytes()==(root/"remotemcp"/"node"/"migrations"/"001_node.sql").read_bytes()
    tables={r[0] for r in db.query_all("select name from sqlite_master where type='table'")}
    assert {"node_meta","node_projects","node_tasks","node_commands","node_cas_mutations","node_routed_jobs"}<=tables


def test_node_schema_checksum_accepts_lf_crlf_equivalence(tmp_path):
    root=Path(__file__).resolve().parents[2]
    canonical=(root/"remotemcp"/"node"/"migrations"/"001_node.sql").read_text(encoding="utf-8")
    crlf_path=tmp_path/"001_node_crlf.sql"
    crlf_path.write_bytes(canonical.replace("\r\n","\n").replace("\n","\r\n").encode("utf-8"))

    runtime=tmp_path/"rt-newline"
    legacy=NodeDatabase(runtime)
    legacy.schema_path=crlf_path
    legacy.bootstrap()

    release=NodeDatabase(runtime)
    release.bootstrap()
    assert release.get_meta("schema")["version"]==1


def test_node_schema_checksum_still_rejects_content_drift(tmp_path):
    root=Path(__file__).resolve().parents[2]
    canonical=(root/"remotemcp"/"node"/"migrations"/"001_node.sql").read_text(encoding="utf-8")
    drift_path=tmp_path/"001_node_drift.sql"
    drift_path.write_text(canonical+"\n-- substantive drift\n",encoding="utf-8")

    runtime=tmp_path/"rt-drift"
    db=NodeDatabase(runtime)
    db.bootstrap()

    drift=NodeDatabase(runtime)
    drift.schema_path=drift_path
    with pytest.raises(DurableError,match="node schema checksum mismatch"):
        drift.bootstrap()
