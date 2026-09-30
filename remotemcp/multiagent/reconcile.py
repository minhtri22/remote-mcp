from __future__ import annotations

from remotemcp.durable.models import now_ms


class MultiAgentReconciler:
    def __init__(self,db,tasks,projects,worktrees,cas,ttl_seconds:int=120):
        self.db=db; self.tasks=tasks; self.projects=projects; self.worktrees=worktrees; self.cas=cas
        self.ttl_ms=ttl_seconds*1000

    def expire_sessions_and_leases(self)->None:
        t=now_ms()
        cutoff=t-self.ttl_ms
        with self.db.transaction() as con:
            con.execute("UPDATE agent_sessions SET state='STALE' WHERE state='ACTIVE' AND last_heartbeat_at_ms<?",(cutoff,))
            expired=con.execute("SELECT * FROM task_leases WHERE expires_at_ms<=?",(t,)).fetchall()
            for lease in expired:
                task=con.execute("SELECT * FROM tasks WHERE task_id=?",(lease["task_id"],)).fetchone()
                con.execute("DELETE FROM path_leases WHERE task_id=?",(lease["task_id"],))
                con.execute("DELETE FROM task_leases WHERE task_id=?",(lease["task_id"],))
                if task and task["state"] in ("CLAIMED","RUNNING"):
                    con.execute(
                        "UPDATE tasks SET state='RECOVERABLE',owner_agent_id=NULL,owner_session_id=NULL,updated_at_ms=? WHERE task_id=?",
                        (t,lease["task_id"]),
                    )
                    con.execute(
                        "INSERT INTO task_events(task_id,agent_id,session_id,event_type,payload_json,created_at_ms) "
                        "VALUES(?,?,?,'LEASE_EXPIRED','{}',?)",
                        (lease["task_id"],lease["agent_id"],lease["session_id"],t),
                    )

    def reconcile_claimed(self)->None:
        t=now_ms()
        rows=self.db.query_all(
            "SELECT t.*,p.root_rel,p.project_kind FROM tasks t JOIN projects p ON p.project_id=t.project_id WHERE t.state='CLAIMED'"
        )
        for task in rows:
            lease=self.db.query_one("SELECT * FROM task_leases WHERE task_id=?",(task["task_id"],))
            if lease is None or lease["expires_at_ms"]<=t:
                continue
            if task["project_kind"]=="GIT":
                try:
                    self.worktrees.provision(self.projects.root_path(task),task)
                except Exception:
                    continue
            with self.db.transaction() as con:
                current=con.execute("SELECT * FROM task_leases WHERE task_id=?",(task["task_id"],)).fetchone()
                row=con.execute("SELECT * FROM tasks WHERE task_id=?",(task["task_id"],)).fetchone()
                if current and row and row["state"]=="CLAIMED" and current["expires_at_ms"]>now_ms():
                    con.execute("UPDATE tasks SET state='RUNNING',updated_at_ms=? WHERE task_id=?",(now_ms(),task["task_id"]))

    def reconcile_running_worktrees(self)->None:
        rows=self.db.query_all(
            "SELECT t.*,p.root_rel,p.project_kind FROM tasks t JOIN projects p ON p.project_id=t.project_id WHERE t.state='RUNNING'"
        )
        for task in rows:
            if task["project_kind"]!="GIT": continue
            try:
                self.worktrees.validate(self.projects.root_path(task),task)
            except Exception:
                with self.db.transaction() as con:
                    con.execute("DELETE FROM path_leases WHERE task_id=?",(task["task_id"],))
                    con.execute("DELETE FROM task_leases WHERE task_id=?",(task["task_id"],))
                    con.execute(
                        "UPDATE tasks SET state='RECOVERABLE',owner_agent_id=NULL,owner_session_id=NULL,cleanup_pending=1,updated_at_ms=? WHERE task_id=?",
                        (now_ms(),task["task_id"]),
                    )

    def reconcile_operations(self)->None:
        rows=self.db.query_all(
            "SELECT * FROM operations WHERE kind='TASK_CLAIM' AND state IN ('RESERVED','EXECUTING') ORDER BY created_at_ms"
        )
        for op in rows:
            task=self.db.query_one("SELECT * FROM tasks WHERE task_id=?",(op["task_id"],))
            if task is None:
                if op["state"]=="RESERVED":
                    self.cas.operations.fail(op["operation_id"],"TRANSPORT_TIMEOUT",{"reason":"claim never reached task mutation"},True)
                else:
                    self.cas.operations.mark_in_doubt(op["operation_id"],{"reason":"TASK_CLAIM has no task evidence"})
                continue
            lease=self.db.query_one("SELECT * FROM task_leases WHERE task_id=?",(task["task_id"],))
            if lease is None:
                self.cas.operations.fail(op["operation_id"],"LEASE_STALE",{"reason":"claim lease no longer exists"},False)
                continue
            if task["state"]=="CLAIMED":
                project=self.projects.get(task["project_id"])
                try:
                    if project["project_kind"]=="GIT":
                        self.worktrees.provision(self.projects.root_path(project),task)
                    if lease["expires_at_ms"]<=now_ms():
                        continue
                    with self.db.transaction() as con:
                        con.execute("UPDATE tasks SET state='RUNNING',updated_at_ms=? WHERE task_id=? AND state='CLAIMED'",(now_ms(),task["task_id"]))
                    task=self.tasks.get(task["task_id"])
                except Exception:
                    continue
            if task["state"]=="RUNNING":
                result={
                    "task_id":task["task_id"],
                    "agent_id":lease["agent_id"],
                    "session_id":lease["session_id"],
                    "lease_epoch":lease["lease_epoch"],
                    "expires_at_ms":lease["expires_at_ms"],
                }
                self.cas.operations.succeed(op["operation_id"],result)

    def reconcile_all(self)->None:
        self.expire_sessions_and_leases()
        self.cas.reconcile_all()
        self.reconcile_operations()
        self.reconcile_claimed()
        self.reconcile_running_worktrees()