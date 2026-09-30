from __future__ import annotations

import json
import secrets

from .db import Database
from .errors import DurableError
from .models import JobState, TERMINAL_JOB_STATES, now_ms


class JobRepository:
    def __init__(self, db: Database):
        self.db = db

    def get(self, job_id: str):
        row = self.db.query_one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        if row is None:
            raise DurableError("NOT_FOUND", "job not found", job_id=job_id)
        return row

    def by_operation(self, operation_id: str):
        return self.db.query_one(
            "SELECT * FROM jobs WHERE operation_id=?",
            (operation_id,),
        )

    def create(
        self,
        operation_id: str,
        command: dict,
        cwd_rel: str,
    ):
        job_id = "job_" + secrets.token_hex(16)
        nonce = "ln_" + secrets.token_urlsafe(24)
        stdout_path = f"jobs/{job_id}/stdout.log"
        stderr_path = f"jobs/{job_id}/stderr.log"
        result_path = f"jobs/{job_id}/result.json"
        t = now_ms()
        with self.db.transaction() as con:
            existing = con.execute(
                "SELECT * FROM jobs WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if existing:
                return existing, False
            con.execute(
                "INSERT INTO jobs("
                "job_id,operation_id,state,command_json,cwd_rel,env_profile,launch_nonce,"
                "stdout_path,stderr_path,result_path,created_at_ms,updated_at_ms"
                ") VALUES(?,?,?,?,?,'safe',?,?,?,?,?,?)",
                (
                    job_id,
                    operation_id,
                    JobState.QUEUED.value,
                    json.dumps(command, ensure_ascii=False, sort_keys=True),
                    cwd_rel,
                    nonce,
                    stdout_path,
                    stderr_path,
                    result_path,
                    t,
                    t,
                ),
            )
            row = con.execute(
                "SELECT * FROM jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            return row, True

    def list_states(self, states: tuple[str, ...]):
        placeholders = ",".join("?" for _ in states)
        return self.db.query_all(
            f"SELECT * FROM jobs WHERE state IN ({placeholders}) ORDER BY created_at_ms,job_id",
            states,
        )

    def count_states(self, states: tuple[str, ...]) -> int:
        placeholders = ",".join("?" for _ in states)
        row = self.db.query_one(
            f"SELECT COUNT(*) AS n FROM jobs WHERE state IN ({placeholders})",
            states,
        )
        return int(row["n"])

    def transition(self, job_id: str, allowed_from: tuple[str, ...], to_state: str, **fields):
        t = now_ms()
        with self.db.transaction() as con:
            row = con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise DurableError("NOT_FOUND", "job not found", job_id=job_id)
            if row["state"] not in allowed_from:
                return row, False
            sets = ["state=?", "updated_at_ms=?", "version=version+1"]
            values = [to_state, t]
            for key, value in fields.items():
                sets.append(f"{key}=?")
                values.append(value)
            values.append(job_id)
            con.execute(
                f"UPDATE jobs SET {','.join(sets)} WHERE job_id=?",
                tuple(values),
            )
            return con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone(), True

    def update_fields(self, job_id: str, **fields):
        if not fields:
            return self.get(job_id)
        t = now_ms()
        with self.db.transaction() as con:
            sets = ["updated_at_ms=?", "version=version+1"]
            values = [t]
            for key, value in fields.items():
                sets.append(f"{key}=?")
                values.append(value)
            values.append(job_id)
            con.execute(
                f"UPDATE jobs SET {','.join(sets)} WHERE job_id=?",
                tuple(values),
            )
            return con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()

    def terminalize(
        self,
        job_id: str,
        state: str,
        *,
        exit_code: int | None,
        error_code: str | None = None,
        error_json: str | None = None,
    ):
        if state not in TERMINAL_JOB_STATES:
            raise ValueError(state)
        t = now_ms()
        with self.db.transaction() as con:
            row = con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise DurableError("NOT_FOUND", "job not found", job_id=job_id)
            if row["state"] in TERMINAL_JOB_STATES:
                return row, False
            con.execute(
                "UPDATE jobs SET state=?,exit_code=?,finished_at_ms=?,updated_at_ms=?,"
                "error_code=?,error_json=?,version=version+1 WHERE job_id=?",
                (state, exit_code, t, t, error_code, error_json, job_id),
            )
            return con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone(), True
