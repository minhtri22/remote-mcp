from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms
from remotemcp.durable.process import atomic_write_bytes


def file_hash(path:Path)->str:
    if not path.exists():return "MISSING"
    return hashlib.sha256(path.read_bytes()).hexdigest()


class NodeCas:
    def __init__(self,db,worktrees,journal):
        self.db=db;self.worktrees=worktrees;self.journal=journal

    def _target(self,task_id:str,path:str):
        root=self.worktrees.execution_root(task_id).resolve()
        target=(root/path).resolve()
        if target!=root and not target.is_relative_to(root):
            raise DurableError("PATH_ESCAPE","node CAS path escapes task root")
        return root,target,target.relative_to(root).as_posix()

    def _command_envelope(self,command_id:str):
        row=self.journal.get(command_id)
        return row,json.loads(row["payload_json"])

    def _reconcile_row(self,row):
        command,_=self._command_envelope(row["command_id"])
        root,target,_=self._target(row["task_id"],row["path_rel"])
        temp=(root/row["temp_rel"]).resolve()
        current=file_hash(target);temp_hash=file_hash(temp)
        if current==row["intended_after_hash"]:
            with self.db.transaction() as con:
                con.execute("UPDATE node_cas_mutations SET state='COMMITTED',updated_at_ms=? WHERE command_id=?",(now_ms(),row["command_id"]))
            result={"task_id":row["task_id"],"path":row["path_rel"],"before_hash":row["expected_before_hash"],"after_hash":current}
            self.journal.terminal(row["command_id"],"SUCCEEDED",result=result)
            return result
        if row["state"]=="PREPARED" and current==row["expected_before_hash"] and temp_hash==row["intended_after_hash"]:
            envelope=json.loads(command["payload_json"])
            if int(envelope["command_expires_at_ms"])<=now_ms():
                self.journal.terminal(row["command_id"],"IN_DOUBT",error_code="DEVICE_COMMAND_EXPIRED",error={"reason":"CAS prepared but command expired"})
                raise DurableError("OPERATION_IN_DOUBT","expired CAS cannot replace")
            os.replace(temp,target)
            with self.db.transaction() as con:
                con.execute("UPDATE node_cas_mutations SET state='REPLACED',updated_at_ms=? WHERE command_id=?",(now_ms(),row["command_id"]))
            return self._reconcile_row(self.db.query_one("SELECT * FROM node_cas_mutations WHERE command_id=?",(row["command_id"],)))
        self.journal.terminal(row["command_id"],"IN_DOUBT",error_code="OPERATION_IN_DOUBT",error={"current_hash":current,"temp_hash":temp_hash})
        raise DurableError("OPERATION_IN_DOUBT","node CAS evidence ambiguous")

    def write(self,command_id:str,payload:dict)->dict:
        task_id=str(payload["task_id"]);path=str(payload["path"])
        expected=str(payload["expected_sha256"]);content=str(payload["content"])
        root,target,path_rel=self._target(task_id,path)
        intended=hashlib.sha256(content.encode("utf-8")).hexdigest()
        old=self.db.query_one("SELECT * FROM node_cas_mutations WHERE command_id=?",(command_id,))
        if old:return self._reconcile_row(old)
        command,envelope=self._command_envelope(command_id)
        if int(envelope["command_expires_at_ms"])<=now_ms():
            raise DurableError("DEVICE_COMMAND_EXPIRED","CAS command expired before prepare")
        before=file_hash(target)
        if before!=expected:
            raise DurableError("CAS_MISMATCH","node CAS expected hash mismatch",expected=expected,actual=before)
        target.parent.mkdir(parents=True,exist_ok=True)
        temp=target.with_name(f".{target.name}.{command_id}.cas.tmp")
        temp_rel=temp.relative_to(root).as_posix()
        t=now_ms()
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO node_cas_mutations(command_id,task_id,path_rel,expected_before_hash,intended_after_hash,temp_rel,state,created_at_ms,updated_at_ms) "
                "VALUES(?,?,?,?,?,?,'PREPARED',?,?)",
                (command_id,task_id,path_rel,before,intended,temp_rel,t,t),
            )
        data=content.encode("utf-8")
        with open(temp,"wb") as f:
            f.write(data)
            f.flush()
            try:os.fsync(f.fileno())
            except OSError:pass
        if file_hash(temp)!=intended:raise DurableError("CAS_MISMATCH","node CAS temp hash mismatch")
        if int(envelope["command_expires_at_ms"])<=now_ms():
            raise DurableError("DEVICE_COMMAND_EXPIRED","CAS command expired before replace")
        current=file_hash(target)
        if current!=before:
            with self.db.transaction() as con:
                con.execute("UPDATE node_cas_mutations SET state='ABORTED',updated_at_ms=? WHERE command_id=?",(now_ms(),command_id))
            raise DurableError("CAS_MISMATCH","node CAS target changed before replace")
        os.replace(temp,target)
        with self.db.transaction() as con:
            con.execute("UPDATE node_cas_mutations SET state='REPLACED',updated_at_ms=? WHERE command_id=?",(now_ms(),command_id))
        return self._reconcile_row(self.db.query_one("SELECT * FROM node_cas_mutations WHERE command_id=?",(command_id,)))

    def edit(self,command_id:str,payload:dict)->dict:
        task_id=str(payload["task_id"]);path=str(payload["path"])
        _,target,_=self._target(task_id,path)
        if not target.exists():raise DurableError("NOT_FOUND","node CAS edit file missing")
        text=target.read_text(encoding="utf-8")
        old=str(payload["old"]);new=str(payload["new"]);expected_occ=int(payload.get("expected_occurrences",1))
        count=text.count(old)
        if count!=expected_occ:raise DurableError("EDIT_MATCH_COUNT","unexpected edit occurrence count",expected=expected_occ,actual=count)
        merged=dict(payload);merged["content"]=text.replace(old,new);merged.pop("old",None);merged.pop("new",None);merged.pop("expected_occurrences",None)
        return self.write(command_id,merged)

    def reconcile_all(self):
        for row in self.db.query_all("SELECT * FROM node_cas_mutations WHERE state IN ('PREPARED','REPLACED') ORDER BY created_at_ms"):
            try:self._reconcile_row(row)
            except DurableError:pass
