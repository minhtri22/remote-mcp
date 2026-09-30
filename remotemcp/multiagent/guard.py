from __future__ import annotations

from pathlib import Path

from remotemcp.durable.errors import DurableError


class LegacyGuard:
    def __init__(self,projects):
        self.projects=projects

    def guard_file_mutation(self,path:Path)->None:
        if self.projects.covering_project_for_path(path) is not None:
            raise DurableError("LEASE_REQUIRED_USE_CAS","managed project mutation requires CAS tool")

    def guard_run_command(self)->None:
        if self.projects.managed_mode():
            raise DurableError("TASK_CONTEXT_REQUIRED","managed mode requires task_job_submit")

    def guard_job_submit(self)->None:
        if self.projects.managed_mode():
            raise DurableError("TASK_CONTEXT_REQUIRED","managed mode requires task_job_submit")

    def guard_job_cancel(self,job_id:str,db)->None:
        row=db.query_one(
            "SELECT o.task_id FROM jobs j JOIN operations o ON o.operation_id=j.operation_id WHERE j.job_id=?",
            (job_id,),
        )
        if row is not None and row["task_id"]:
            task=db.query_one("SELECT 1 FROM tasks WHERE task_id=?",(row["task_id"],))
            if task is not None:
                raise DurableError("TASK_CONTEXT_REQUIRED","managed task job requires task_job_cancel")