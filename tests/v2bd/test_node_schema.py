from __future__ import annotations
from pathlib import Path
from remotemcp.node.db import NodeDatabase

def test_node_schema_bootstrap_repeat_and_byte_identity(tmp_path):
    db=NodeDatabase(tmp_path/"rt");db.bootstrap();db.bootstrap()
    assert db.get_meta("schema")["version"]==1
    root=Path(__file__).resolve().parents[2]
    assert (root/"specs"/"v2bd_node_schema_v1.sql").read_bytes()==(root/"remotemcp"/"node"/"migrations"/"001_node.sql").read_bytes()
    tables={r[0] for r in db.query_all("select name from sqlite_master where type='table'")}
    assert {"node_meta","node_projects","node_tasks","node_commands","node_cas_mutations","node_routed_jobs"}<=tables
