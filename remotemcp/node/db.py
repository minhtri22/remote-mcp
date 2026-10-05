from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from remotemcp.durable.errors import DurableError


class NodeDatabase:
    def __init__(self,runtime_dir:Path):
        self.runtime_dir=runtime_dir.resolve()
        self.path=self.runtime_dir/"node.db"
        self.schema_path=Path(__file__).resolve().parent/"migrations"/"001_node.sql"

    def connect(self):
        con=sqlite3.connect(self.path,timeout=5.0,isolation_level=None,check_same_thread=False)
        con.row_factory=sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    @staticmethod
    def _schema_checksum_candidates(sql_bytes:bytes)->set[str]:
        """Return raw and newline-equivalent SHA-256 identities.

        Node schema history is content-immutable, but Windows checkouts can
        materialize the same UTF-8 SQL text with CRLF while git archives use
        LF. Accept only that line-ending equivalence; all other content drift
        remains startup-fatal.
        """
        candidates={hashlib.sha256(sql_bytes).hexdigest()}
        try:
            text=sql_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return candidates
        normalized=text.replace("\r\n","\n").replace("\r","\n")
        candidates.add(hashlib.sha256(normalized.encode("utf-8")).hexdigest())
        candidates.add(hashlib.sha256(normalized.replace("\n","\r\n").encode("utf-8")).hexdigest())
        return candidates

    def bootstrap(self):
        self.runtime_dir.mkdir(parents=True,exist_ok=True)
        raw=self.schema_path.read_bytes()
        checksum=hashlib.sha256(raw).hexdigest()
        checksum_candidates=self._schema_checksum_candidates(raw)
        con=self.connect()
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            version=int(con.execute("PRAGMA user_version").fetchone()[0])
            if version==0:
                con.execute("BEGIN IMMEDIATE")
                try:
                    for part in raw.decode("utf-8").split(";"):
                        stmt=part.strip()
                        if stmt:
                            con.execute(stmt)
                    con.execute(
                        "INSERT INTO node_meta(key,value_json) VALUES('schema',?)",
                        (json.dumps({"version":1,"sha256":checksum},sort_keys=True),),
                    )
                    con.execute("PRAGMA user_version=1")
                    con.execute("COMMIT")
                except Exception:
                    try: con.execute("ROLLBACK")
                    except sqlite3.Error: pass
                    raise
            elif version==1:
                row=con.execute("SELECT value_json FROM node_meta WHERE key='schema'").fetchone()
                if row is None:
                    raise DurableError("NODE_SCHEMA_MISMATCH","node schema metadata missing")
                meta=json.loads(row[0])
                if int(meta.get("version",0))!=1 or meta.get("sha256") not in checksum_candidates:
                    raise DurableError("NODE_SCHEMA_MISMATCH","node schema checksum mismatch")
            else:
                raise DurableError("NODE_SCHEMA_MISMATCH","unsupported node schema version",version=version)
        finally:
            con.close()

    @contextmanager
    def transaction(self):
        con=self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.execute("COMMIT")
        except sqlite3.OperationalError as exc:
            try:con.execute("ROLLBACK")
            except sqlite3.Error:pass
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise DurableError("DB_BUSY",str(exc)) from exc
            raise
        except Exception:
            try:con.execute("ROLLBACK")
            except sqlite3.Error:pass
            raise
        finally:
            con.close()

    def query_one(self,sql,params=()):
        con=self.connect()
        try:return con.execute(sql,params).fetchone()
        finally:con.close()

    def query_all(self,sql,params=()):
        con=self.connect()
        try:return con.execute(sql,params).fetchall()
        finally:con.close()

    def set_meta(self,key:str,value:dict):
        with self.transaction() as con:
            con.execute(
                "INSERT INTO node_meta(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (key,json.dumps(value,sort_keys=True)),
            )

    def get_meta(self,key:str):
        row=self.query_one("SELECT value_json FROM node_meta WHERE key=?",(key,))
        return json.loads(row["value_json"]) if row else None
