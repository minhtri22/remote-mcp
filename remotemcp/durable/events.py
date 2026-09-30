from __future__ import annotations

import json
import sqlite3

from .db import Database
from .models import now_ms


class EventRepository:
    def __init__(self, db: Database):
        self.db = db

    def append(
        self,
        event_type: str,
        payload: dict,
        *,
        job_id: str | None = None,
        operation_id: str | None = None,
        terminal: bool = False,
    ) -> int:
        with self.db.transaction() as con:
            if terminal and job_id:
                existing = con.execute(
                    "SELECT event_id FROM events WHERE job_id=? AND terminal=1",
                    (job_id,),
                ).fetchone()
                if existing:
                    return int(existing["event_id"])
            cur = con.execute(
                "INSERT INTO events(job_id,operation_id,event_type,terminal,payload_json,created_at_ms) "
                "VALUES(?,?,?,?,?,?)",
                (
                    job_id,
                    operation_id,
                    event_type,
                    1 if terminal else 0,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    now_ms(),
                ),
            )
            event_id = int(cur.lastrowid)
            if terminal and job_id:
                con.execute(
                    "UPDATE jobs SET terminal_event_id=?,updated_at_ms=? WHERE job_id=?",
                    (event_id, now_ms(), job_id),
                )
            return event_id

    def ensure_terminal(
        self,
        job_id: str,
        operation_id: str,
        state: str,
        payload: dict,
    ) -> int:
        try:
            return self.append(
                "JOB_TERMINAL",
                {"state": state, **payload},
                job_id=job_id,
                operation_id=operation_id,
                terminal=True,
            )
        except sqlite3.IntegrityError:
            row = self.db.query_one(
                "SELECT event_id FROM events WHERE job_id=? AND terminal=1",
                (job_id,),
            )
            if row is None:
                raise
            return int(row["event_id"])

    def terminal_event(self, job_id: str):
        return self.db.query_one(
            "SELECT * FROM events WHERE job_id=? AND terminal=1 ORDER BY event_id LIMIT 1",
            (job_id,),
        )

    def cursor(self, subscriber_id: str, job_id: str) -> int:
        row = self.db.query_one(
            "SELECT ack_event_id FROM event_cursors WHERE subscriber_id=? AND job_id=?",
            (subscriber_id, job_id),
        )
        return int(row["ack_event_id"]) if row else 0

    def ack(self, subscriber_id: str, job_id: str, event_id: int) -> int:
        t = now_ms()
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO event_cursors(subscriber_id,job_id,ack_event_id,updated_at_ms) "
                "VALUES(?,?,?,?) "
                "ON CONFLICT(subscriber_id,job_id) DO UPDATE SET "
                "ack_event_id=MAX(event_cursors.ack_event_id,excluded.ack_event_id),"
                "updated_at_ms=excluded.updated_at_ms",
                (subscriber_id, job_id, event_id, t),
            )
            row = con.execute(
                "SELECT ack_event_id FROM event_cursors WHERE subscriber_id=? AND job_id=?",
                (subscriber_id, job_id),
            ).fetchone()
            return int(row["ack_event_id"])
