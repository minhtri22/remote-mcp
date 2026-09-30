from __future__ import annotations

import hashlib
import os
from pathlib import Path

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms
from remotemcp.durable.process import atomic_write_bytes


def sha256_bytes(data:bytes)->str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path:Path)->str:
    if not path.exists():
        return "MISSING"
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CasService:
    def __init__(self,db,operations,leases,workspace_root:Path,task_execution_root_resolver):
        self.db=db
        self.operations=operations
        self.leases=leases
        self.workspace_root=workspace_root.resolve()
        self._root=task_execution_root_resolver

    def _resolve(self,task_id:str,path:str)->tuple[Path,str]:
        root=self._root(task_id).resolve()
        target=(root/path).resolve()
        if target!=root and not target.is_relative_to(root):
            raise DurableError("PATH_ESCAPE","CAS path escapes task root")
        return target,target.relative_to(root).as_posix()

    def _prepare(self,operation_id:str,task_id:str,path_rel:str,before:str,after:str,temp_rel:str)->None:
        t=now_ms()
        with self.db.transaction() as con:
            existing=con.execute("SELECT * FROM cas_mutations WHERE operation_id=?",(operation_id,)).fetchone()
            if existing is None:
                conflict=con.execute(
                    "SELECT operation_id FROM cas_mutations WHERE task_id=? AND path_rel=? "
                    "AND state IN ('PREPARED','REPLACED') AND operation_id<>? LIMIT 1",
                    (task_id,path_rel,operation_id),
                ).fetchone()
                if conflict is not None:
                    raise DurableError(
                        "CAS_MISMATCH",
                        "another CAS mutation is active for the same task/path",
                        conflicting_operation_id=conflict["operation_id"],
                    )
                con.execute(
                    "INSERT INTO cas_mutations(operation_id,task_id,path_rel,expected_before_hash,intended_after_hash,temp_rel,state,created_at_ms,updated_at_ms) "
                    "VALUES(?,?,?,?,?,?,'PREPARED',?,?)",
                    (operation_id,task_id,path_rel,before,after,temp_rel,t,t),
                )

    def write(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,path:str,expected_sha256:str,content:str)->dict:
        self.leases.validate(task_id,lease_token,lease_epoch)
        target,path_rel=self._resolve(task_id,path)
        task=self.db.query_one("SELECT * FROM tasks WHERE task_id=?",(task_id,))
        project=self.db.query_one("SELECT * FROM projects WHERE project_id=?",(task["project_id"],))
        if project["project_kind"]=="NON_GIT" and not self.leases.has_covering(task_id,path_rel):
            raise DurableError("LEASE_REQUIRED","non-Git CAS requires covering path lease")

        data=content.encode("utf-8")
        intended=sha256_bytes(data)
        normalized={"task_id":task_id,"path":path_rel,"expected_sha256":expected_sha256,"content_sha256":intended}
        op,created=self.operations.reserve(operation_id,"FILE_WRITE_CAS",normalized,agent_id=task["owner_agent_id"] or "",project_id=task["project_id"],task_id=task_id)
        if not created:
            replay=self.operations.replay_result(op)
            if replay is not None:
                return {**replay,"replayed":True}
            row=self.db.query_one("SELECT * FROM cas_mutations WHERE operation_id=?",(operation_id,))
            if row:
                return self.reconcile_one(row)
            raise DurableError("OPERATION_IN_DOUBT","CAS operation requires reconciliation")

        self.operations.mark_executing(operation_id)
        before=file_hash(target)
        if before!=expected_sha256:
            self.operations.fail(operation_id,"CAS_MISMATCH",{"expected":expected_sha256,"actual":before},False)
            raise DurableError("CAS_MISMATCH","current file hash does not match",expected=expected_sha256,actual=before)

        temp=target.with_name(f".{target.name}.{operation_id}.cas.tmp")
        root=self._root(task_id).resolve()
        temp_rel=temp.relative_to(root).as_posix()
        self._prepare(operation_id,task_id,path_rel,before,intended,temp_rel)
        target.parent.mkdir(parents=True,exist_ok=True)
        atomic_write_bytes(temp,data)
        if file_hash(temp)!=intended:
            raise DurableError("CAS_MISMATCH","temp hash mismatch")

        self.leases.validate(task_id,lease_token,lease_epoch)
        current=file_hash(target)
        if current!=before:
            with self.db.transaction() as con:
                con.execute("UPDATE cas_mutations SET state='ABORTED',updated_at_ms=? WHERE operation_id=?",(now_ms(),operation_id))
            self.operations.fail(operation_id,"CAS_MISMATCH",{"expected":before,"actual":current},False)
            try: temp.unlink(missing_ok=True)
            except OSError: pass
            raise DurableError("CAS_MISMATCH","file changed before replace",expected=before,actual=current)

        os.replace(temp,target)
        with self.db.transaction() as con:
            con.execute("UPDATE cas_mutations SET state='REPLACED',updated_at_ms=? WHERE operation_id=?",(now_ms(),operation_id))
        after=file_hash(target)
        if after!=intended:
            self.operations.mark_in_doubt(operation_id,{"reason":"after hash mismatch","expected":intended,"actual":after})
            raise DurableError("OPERATION_IN_DOUBT","CAS after-hash mismatch")
        result={"task_id":task_id,"path":path_rel,"before_hash":before,"after_hash":after}
        with self.db.transaction() as con:
            con.execute("UPDATE cas_mutations SET state='COMMITTED',updated_at_ms=? WHERE operation_id=?",(now_ms(),operation_id))
        self.operations.succeed(operation_id,result)
        return result

    def edit(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,path:str,expected_sha256:str,old:str,new:str,expected_occurrences:int=1)->dict:
        target,_=self._resolve(task_id,path)
        if not target.exists():
            raise DurableError("NOT_FOUND","file not found")
        text=target.read_text(encoding="utf-8")
        count=text.count(old)
        if count!=expected_occurrences:
            raise DurableError("EDIT_MATCH_COUNT","unexpected old-text occurrence count",expected=expected_occurrences,actual=count)
        return self.write(operation_id,task_id,lease_token,lease_epoch,path,expected_sha256,text.replace(old,new))

    def reconcile_one(self,row)->dict:
        task_id=row["task_id"]; operation_id=row["operation_id"]
        target,path_rel=self._resolve(task_id,row["path_rel"])
        root=self._root(task_id).resolve()
        temp=(root/row["temp_rel"]).resolve()
        current=file_hash(target)
        temp_hash=file_hash(temp) if temp.exists() else "MISSING"
        state=row["state"]
        if state=="COMMITTED":
            op=self.operations.get(operation_id)
            replay=self.operations.replay_result(op)
            return {**(replay or {}),"replayed":True}
        if state=="ABORTED":
            raise DurableError("CAS_MISMATCH","CAS mutation previously aborted")
        if current==row["intended_after_hash"]:
            with self.db.transaction() as con:
                con.execute("UPDATE cas_mutations SET state='COMMITTED',updated_at_ms=? WHERE operation_id=?",(now_ms(),operation_id))
            result={"task_id":task_id,"path":path_rel,"before_hash":row["expected_before_hash"],"after_hash":current}
            self.operations.succeed(operation_id,result)
            return {**result,"reconciled":True}
        if state=="PREPARED" and current==row["expected_before_hash"] and temp_hash==row["intended_after_hash"]:
            # Only resume a new replace while the original task lease epoch is still current.
            op=self.operations.get(operation_id)
            lease=self.db.query_one("SELECT * FROM task_leases WHERE task_id=?",(task_id,))
            task=self.db.query_one("SELECT * FROM tasks WHERE task_id=?",(task_id,))
            if lease is None or task is None or op["agent_id"]!=task["owner_agent_id"] or int(task["lease_epoch"])!=int(lease["lease_epoch"]):
                self.operations.mark_in_doubt(operation_id,{"reason":"lease not current for PREPARED CAS"})
                raise DurableError("OPERATION_IN_DOUBT","CAS lease is no longer current")
            os.replace(temp,target)
            with self.db.transaction() as con:
                con.execute("UPDATE cas_mutations SET state='REPLACED',updated_at_ms=? WHERE operation_id=?",(now_ms(),operation_id))
            return self.reconcile_one(self.db.query_one("SELECT * FROM cas_mutations WHERE operation_id=?",(operation_id,)))
        self.operations.mark_in_doubt(operation_id,{"reason":"CAS evidence ambiguous","current_hash":current,"temp_hash":temp_hash})
        raise DurableError("OPERATION_IN_DOUBT","CAS evidence is ambiguous")

    def reconcile_all(self)->None:
        for row in self.db.query_all("SELECT * FROM cas_mutations WHERE state IN ('PREPARED','REPLACED') ORDER BY created_at_ms"):
            try: self.reconcile_one(row)
            except DurableError: pass