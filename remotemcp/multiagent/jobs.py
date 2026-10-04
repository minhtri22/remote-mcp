from __future__ import annotations

from pathlib import Path

from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import TERMINAL_JOB_STATES


class TaskJobService:
    def __init__(
        self,db,durable,leases,projects,tasks,worktrees,
        execution_root_resolver,admissions,
    ):
        self.db=db; self.durable=durable; self.leases=leases
        self.projects=projects; self.tasks=tasks; self.worktrees=worktrees
        self._root=execution_root_resolver
        self.admissions=admissions

    def _local_rows(self,task_id:str):
        return self.db.query_all(
            "SELECT j.*,o.task_id,o.operation_id AS op_id "
            "FROM jobs j JOIN operations o ON o.operation_id=j.operation_id "
            "WHERE o.task_id=? ORDER BY j.created_at_ms,j.job_id",
            (task_id,),
        )

    def _terminal_evidence(self,job_id:str)->dict:
        out=self.durable.job_result(job_id)
        if not out.get("terminal") or not out.get("terminal_event_id"):
            raise DurableError(
                "PREDECESSOR_STATE_UNRESOLVED",
                "terminal local job lacks authoritative terminal event evidence",
                job_id=job_id,
            )
        return out

    def _reconcile_lane(self,task_id:str,current_operation_id:str|None=None):
        # Durable reconciliation is authoritative for local managed jobs.
        self.durable.reconciler.reconcile_jobs()

        active=self.admissions.active(task_id,current_operation_id)
        if active is not None:
            job_id=active["job_id"]
            if not job_id:
                row=self.durable.jobs.by_operation(active["operation_id"])
                if row is None:
                    raise DurableError(
                        "PREDECESSOR_STATE_UNRESOLVED",
                        "active task-lane admission has no durable job identity",
                        predecessor_operation_id=active["operation_id"],
                    )
                job_id=row["job_id"]
                self.admissions.bind(active["operation_id"],job_id)
            row=self.durable.jobs.get(job_id)
            if row["state"] not in TERMINAL_JOB_STATES:
                raise DurableError(
                    "PREDECESSOR_JOB_NOT_TERMINAL",
                    "task lane predecessor is still non-terminal",
                    predecessor_job_id=job_id,
                    predecessor_state=row["state"],
                )
            evidence=self._terminal_evidence(job_id)
            self.admissions.terminalize(active["operation_id"],row["state"],evidence)

        rows=[
            r for r in self._local_rows(task_id)
            if not current_operation_id or r["op_id"]!=current_operation_id
        ]
        for row in rows:
            if row["state"] not in TERMINAL_JOB_STATES:
                raise DurableError(
                    "PREDECESSOR_JOB_NOT_TERMINAL",
                    "legacy/local predecessor is still non-terminal",
                    predecessor_job_id=row["job_id"],
                    predecessor_state=row["state"],
                )

        if rows:
            latest=rows[-1]
            self._terminal_evidence(latest["job_id"])
            return latest["job_id"]
        return None

    async def submit(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,argv:list[str],cwd:str=".")->dict:
        lease=self.leases.validate(task_id,lease_token,lease_epoch)
        task=self.tasks.get(task_id)
        project=self.projects.get(task["project_id"])
        root=self._root(task_id)
        if project["project_kind"]=="GIT":
            self.worktrees.validate(self.projects.root_path(project),task)
        else:
            if not self.leases.has_covering(task_id,".",require_root_tree=True):
                raise DurableError("LEASE_REQUIRED","NON_GIT task job requires project-root TREE lease")
        target=(root/cwd).resolve()
        if target!=root and not target.is_relative_to(root):
            raise DurableError("PATH_ESCAPE","job cwd escapes task root")
        rel_to_workspace=target.relative_to(self.durable.config.workspace_root).as_posix()

        existing_admission=self.admissions.by_operation(operation_id)
        existing_job=self.durable.jobs.by_operation(operation_id)
        if existing_admission is None and existing_job is None:
            predecessor=self._reconcile_lane(task_id)
            self.admissions.reserve(
                task_id,operation_id,"LOCAL",predecessor_job_id=predecessor,
            )
        elif existing_admission is not None:
            # Exact operation replay/resume is the same admitted job, not a successor.
            if existing_admission["task_id"]!=task_id:
                raise DurableError(
                    "TASK_JOB_LANE_STATE_MISMATCH",
                    "operation replay targets a different task lane",
                )

        try:
            result=await self.durable.job_submit(
                operation_id,argv,rel_to_workspace,
                agent_id=lease["agent_id"],project_id=task["project_id"],task_id=task_id,
            )
            if self.admissions.by_operation(operation_id) is not None:
                self.admissions.bind(operation_id,result["job_id"])
                if result["state"] in TERMINAL_JOB_STATES:
                    evidence=self._terminal_evidence(result["job_id"])
                    self.admissions.terminalize(operation_id,result["state"],evidence)
            return result
        except Exception:
            row=self.durable.jobs.by_operation(operation_id)
            op=self.durable.operations.get(operation_id)
            admission=self.admissions.by_operation(operation_id)
            if admission is not None:
                if row is not None:
                    self.admissions.bind(operation_id,row["job_id"])
                elif op is not None and op["state"]=="FAILED_FINAL":
                    self.admissions.abort_if_unbound(
                        operation_id,op["error_code"] or "FAILED_FINAL",
                    )
            raise

    def list(self,task_id:str)->dict:
        self.tasks.get(task_id)
        return {"task_id":task_id,"jobs":self.tasks.jobs(task_id)}

    async def cancel(self,operation_id:str,task_id:str,lease_token:str,lease_epoch:int,job_id:str)->dict:
        lease=self.leases.validate(task_id,lease_token,lease_epoch)
        row=self.db.query_one(
            "SELECT o.task_id,o.project_id FROM jobs j JOIN operations o ON o.operation_id=j.operation_id WHERE j.job_id=?",
            (job_id,),
        )
        if row is None: raise DurableError("NOT_FOUND","job not found")
        if row["task_id"]!=task_id:
            raise DurableError("FORBIDDEN","job does not belong to task")
        return await self.durable.job_cancel(
            operation_id,job_id,agent_id=lease["agent_id"],project_id=row["project_id"],task_id=task_id
        )
