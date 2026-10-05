from __future__ import annotations

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms


class BindingRepository:
    def __init__(self,db,devices):
        self.db=db; self.devices=devices

    def project_binding(self,project_id:str):
        return self.db.query_one("SELECT * FROM project_device_bindings WHERE project_id=?",(project_id,))

    def require_active_project_binding(self,project_id:str):
        row=self.project_binding(project_id)
        if row is None:
            return None
        if "lifecycle_state" in row.keys() and row["lifecycle_state"]!="ACTIVE":
            raise DurableError(
                "PROJECT_BINDING_HISTORICAL",
                "project binding is historical and cannot accept new scientific tasks",
                project_id=project_id,
                superseded_by_project_id=row["superseded_by_project_id"],
            )
        return row

    def deprecate_project(self,project_id:str,superseded_by_project_id:str|None=None):
        t=now_ms()
        with self.db.transaction() as con:
            row=con.execute(
                "SELECT * FROM project_device_bindings WHERE project_id=?",(project_id,)
            ).fetchone()
            if row is None:
                raise DurableError("NOT_FOUND","project binding not found",project_id=project_id)
            if superseded_by_project_id:
                successor=con.execute(
                    "SELECT * FROM project_device_bindings WHERE project_id=?",
                    (superseded_by_project_id,),
                ).fetchone()
                if successor is None:
                    raise DurableError(
                        "NOT_FOUND","superseding project binding not found",
                        project_id=superseded_by_project_id,
                    )
                if (
                    "lifecycle_state" in successor.keys()
                    and successor["lifecycle_state"]!="ACTIVE"
                ):
                    raise DurableError(
                        "PROJECT_BINDING_HISTORICAL",
                        "superseding project binding must be active",
                        project_id=superseded_by_project_id,
                    )
            con.execute(
                "UPDATE project_device_bindings "
                "SET lifecycle_state='HISTORICAL',superseded_by_project_id=?,updated_at_ms=? "
                "WHERE project_id=?",
                (superseded_by_project_id,t,project_id),
            )
        return self.project_binding(project_id)

    def task_binding(self,task_id:str):
        return self.db.query_one("SELECT * FROM task_device_bindings WHERE task_id=?",(task_id,))

    def require_task_binding(self,task_id:str):
        row=self.task_binding(task_id)
        if row is None:
            raise DurableError("DEVICE_CONTEXT_REQUIRED","task is not device-bound")
        return row

    def bind_project(self,project_id:str,device_id:str,node_root_rel:str,*,allow_initial:bool=True):
        self.devices.require_online(device_id)
        t=now_ms()
        with self.db.transaction() as con:
            project=con.execute("SELECT * FROM projects WHERE project_id=?",(project_id,)).fetchone()
            if project is None:
                raise DurableError("NOT_FOUND","project not found")
            old=con.execute("SELECT * FROM project_device_bindings WHERE project_id=?",(project_id,)).fetchone()
            if old is None:
                if not allow_initial:
                    raise DurableError("PROJECT_DEVICE_BINDING_CONFLICT","project has no existing binding")
                try:
                    con.execute(
                        "INSERT INTO project_device_bindings(project_id,device_id,node_root_rel,binding_generation,bound_at_ms,updated_at_ms) VALUES(?,?,?,1,?,?)",
                        (project_id,device_id,node_root_rel,t,t),
                    )
                except Exception as exc:
                    if "UNIQUE" in str(exc).upper():
                        raise DurableError("PROJECT_DEVICE_BINDING_CONFLICT","device/path already bound") from exc
                    raise
            elif old["device_id"]==device_id and old["node_root_rel"]==node_root_rel:
                return old
            else:
                nonterminal=con.execute(
                    "SELECT COUNT(*) AS n FROM tasks WHERE project_id=? AND state NOT IN ('COMPLETED','FAILED','CANCELLED')",
                    (project_id,),
                ).fetchone()["n"]
                routed=con.execute(
                    "SELECT COUNT(*) AS n FROM routed_jobs WHERE project_id=? AND last_known_state NOT IN ('SUCCEEDED','FAILED','CANCELLED','LOST')",
                    (project_id,),
                ).fetchone()["n"]
                bound_tasks=con.execute("SELECT COUNT(*) AS n FROM task_device_bindings WHERE project_id=?",(project_id,)).fetchone()["n"]
                if nonterminal or routed or bound_tasks:
                    raise DurableError("PROJECT_DEVICE_MIGRATION_FORBIDDEN","project has task/job device state")
                olddev=self.devices.get(old["device_id"])
                if olddev["state"]!="ONLINE":
                    raise DurableError("PROJECT_DEVICE_MIGRATION_FORBIDDEN","old device is offline/revoked")
                try:
                    con.execute(
                        "UPDATE project_device_bindings SET device_id=?,node_root_rel=?,binding_generation=binding_generation+1,updated_at_ms=? WHERE project_id=?",
                        (device_id,node_root_rel,t,project_id),
                    )
                except Exception as exc:
                    if "UNIQUE" in str(exc).upper():
                        raise DurableError("PROJECT_DEVICE_BINDING_CONFLICT","device/path already bound") from exc
                    raise
            return con.execute("SELECT * FROM project_device_bindings WHERE project_id=?",(project_id,)).fetchone()

    def inherit_task(self,con,task_id:str,project_id:str):
        binding=con.execute("SELECT * FROM project_device_bindings WHERE project_id=?",(project_id,)).fetchone()
        if binding is None:
            return None
        t=now_ms()
        con.execute(
            "INSERT INTO task_device_bindings(task_id,project_id,device_id,binding_generation,inherited_at_ms) VALUES(?,?,?,?,?)",
            (task_id,project_id,binding["device_id"],int(binding["binding_generation"]),t),
        )
        return con.execute("SELECT * FROM task_device_bindings WHERE task_id=?",(task_id,)).fetchone()
