"""Shared Postgres access for the swarm: one connection per agent process.

Every agent reads and writes the live Supabase operational tables through here.
Derived figures come from the dbt marts; raw state comes from the tables.
"""
from __future__ import annotations

import os
import datetime
import json
from typing import Any

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv()

_DB_URL = os.environ.get("DATABASE_URL")


def connect() -> psycopg.Connection:
    if not _DB_URL:
        raise RuntimeError("DATABASE_URL not set in .env")
    conn = psycopg.connect(_DB_URL, autocommit=True, row_factory=dict_row)
    return conn


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def fetchall(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetchone(conn: psycopg.Connection, sql: str, params: tuple = ()) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(conn: psycopg.Connection, sql: str, params: tuple = ()) -> None:
    with conn.cursor() as cur:
        cur.execute(sql, params)


def kill_switch_engaged(conn: psycopg.Connection) -> bool:
    row = fetchone(conn, "select halted from kill_switch where id = 1")
    return bool(row and row["halted"])


def log_decision(
    conn: psycopg.Connection,
    agent: str,
    action: str,
    rationale: str = "",
    cycle: int | None = None,
    sim_time: str | None = None,
    state_summary: dict | None = None,
    tasks_created: Any = None,
    approvals_requested: Any = None,
) -> None:
    """Append one audit row to decisions_log. The dashboard activity feed reads this."""
    execute(
        conn,
        """
        insert into decisions_log
            (agent, cycle, sim_time, action, rationale, state_summary,
             tasks_created, approvals_requested)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            agent,
            cycle,
            sim_time,
            action,
            rationale,
            json.dumps(state_summary) if state_summary is not None else None,
            json.dumps(tasks_created) if tasks_created is not None else None,
            json.dumps(approvals_requested) if approvals_requested is not None else None,
        ),
    )
