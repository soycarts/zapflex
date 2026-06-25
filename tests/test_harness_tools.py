"""Tests for the swarm's task-board tools: dedupe, status normalisation, allowlists.

These guard the contract that lets all agent work show up as a clean lifecycle on
the live tasks board rather than a flood of duplicate todos.
"""
import pytest

from agents import tools
from agents.tools import Ctx, ALLOWLIST, call, actor_name, _norm_status


class FakeDB:
    """A tiny in-memory stand-in for agents.db, enough to exercise the task tools.

    Branches on SQL keywords: the dedupe SELECT, the INSERT, and the UPDATE used by
    create_task / update_task. Records every execute so tests can assert on params.
    """

    def __init__(self):
        self.tasks: list[dict] = []
        self._next_id = 1
        self.executed: list[tuple[str, tuple]] = []

    def fetchone(self, conn, sql, params=()):
        s = " ".join(sql.lower().split())
        if "select id from tasks where lower(title)" in s:
            title = params[0]
            for t in self.tasks:
                if t["title"].lower() == str(title).lower() and t["status"] in ("todo", "doing"):
                    return {"id": t["id"]}
            return None
        if "insert into tasks" in s:
            title, category, priority, created_by_name, owner = params
            row = {"id": self._next_id, "title": title, "category": category,
                   "priority": priority, "created_by_name": created_by_name,
                   "assigned_to": owner, "status": "todo"}
            self.tasks.append(row)
            self._next_id += 1
            return {"id": row["id"]}
        return None

    def fetchall(self, conn, sql, params=()):
        return []

    def execute(self, conn, sql, params=()):
        self.executed.append((sql, params))
        s = " ".join(sql.lower().split())
        if "update tasks set status" in s:
            status, task_id = params[0], params[-1]
            for t in self.tasks:
                if t["id"] == task_id:
                    t["status"] = status


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(tools, "db", db)
    return db


def _ctx(agent="ceo"):
    return Ctx(conn=object(), fleet=None, cycle=1, agent=agent)


# --- create_task dedupe --------------------------------------------------------

def test_create_task_inserts_once(fake_db):
    r = tools.create_task(_ctx("ceo"), title="Improve gamma_ev_solar performance")
    assert r["task_id"] == 1
    assert not r.get("deduped")
    assert len(fake_db.tasks) == 1
    assert fake_db.tasks[0]["created_by_name"] == "agent-ceo"


def test_create_task_dedupes_open_duplicate(fake_db):
    """Re-filing the same open task returns the existing row, no new insert."""
    first = tools.create_task(_ctx("ceo"), title="Improve gamma_ev_solar performance")
    second = tools.create_task(_ctx("trading"), title="improve GAMMA_ev_solar performance")
    assert second["deduped"] is True
    assert second["task_id"] == first["task_id"]
    assert len(fake_db.tasks) == 1   # still only one row on the board


def test_create_task_refiles_after_close(fake_db):
    """A new task with the same title is allowed once the prior one is closed."""
    tools.create_task(_ctx("ceo"), title="Onboard a flexible home")
    fake_db.tasks[0]["status"] = "done"
    r = tools.create_task(_ctx("growth"), title="Onboard a flexible home")
    assert r.get("deduped") is not True
    assert len(fake_db.tasks) == 2


# --- update_task status normalisation -----------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("in_progress", "doing"), ("In Progress", "doing"), ("started", "doing"),
    ("completed", "done"), ("complete", "done"), ("DONE", "done"),
    ("cancel", "cancelled"), ("canceled", "cancelled"),
    ("todo", "todo"), ("doing", "doing"),
])
def test_norm_status(raw, expected):
    assert _norm_status(raw) == expected


def test_update_task_normalises_status(fake_db):
    tools.create_task(_ctx("trading"), title="Learn ev_42 routine")
    tools.update_task(_ctx("trading"), task_id=1, status="in_progress")
    tools.update_task(_ctx("trading"), task_id=1, status="completed")
    statuses = [p[0] for (_sql, p) in fake_db.executed]
    assert statuses == ["doing", "done"]
    assert fake_db.tasks[0]["status"] == "done"


# --- allowlists ----------------------------------------------------------------

def test_every_agent_can_file_and_close_tasks():
    for agent in ("ceo", "trading", "finance", "growth", "support"):
        assert "create_task" in ALLOWLIST[agent], agent
        assert "update_task" in ALLOWLIST[agent], agent


def test_call_enforces_allowlist():
    # Support may now manage tasks...
    assert call(_ctx("support"), "update_task") is tools.update_task
    # ...but still cannot reach another agent's tool.
    with pytest.raises(PermissionError):
        call(_ctx("support"), "book_revenue")


def test_actor_name():
    assert actor_name("trading") == "agent-trading"
