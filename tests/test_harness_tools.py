"""Tests for the swarm's task-board tools: dedupe, status normalisation, allowlists.

These guard the contract that lets all agent work show up as a clean lifecycle on
the live tasks board rather than a flood of duplicate todos.
"""
import pytest

from agents import tools, scripted
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
        s = " ".join(sql.lower().split())
        # The Trading sweep's lookup of its open board tasks.
        if "from tasks" in s and "assigned_to='agent-trading'" in s:
            return [{"id": t["id"], "title": t["title"]} for t in self.tasks
                    if t["status"] in ("todo", "doing")
                    and t.get("assigned_to") == "agent-trading"]
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


# --- trading sweep closes completed/stale board tasks --------------------------

class _Cust:
    def __init__(self, handle, model, has_solar=False, has_ev=False):
        self.handle = handle
        self.model = model
        self.household = {"has_solar": has_solar, "has_ev": has_ev}


class _Fleet:
    def __init__(self, customers):
        self.customers = customers


@pytest.fixture
def trading_db(monkeypatch):
    """FakeDB wired into both tools and scripted, the two modules the sweep touches."""
    db = FakeDB()
    monkeypatch.setattr(tools, "db", db)
    monkeypatch.setattr(scripted, "db", db)
    return db


def _seed_task(db, title, assigned_to="agent-trading", status="todo"):
    row = {"id": db._next_id, "title": title, "assigned_to": assigned_to, "status": status}
    db.tasks.append(row)
    db._next_id += 1
    return row["id"]


def test_best_fit_maps_household_to_ceiling():
    assert scripted._best_fit({"has_ev": True, "has_solar": True}) == "learned"
    assert scripted._best_fit({"has_ev": False, "has_solar": True}) == "seasonal"
    assert scripted._best_fit({"has_ev": False, "has_solar": False}) == "naive"


def test_sweep_closes_all_tasks_for_best_fit_home(trading_db):
    """The CEO files near-duplicate tasks per home; the sweep closes every one
    once the home has reached its best-fit forecast — not just a single task."""
    _seed_task(trading_db, "Trading: learn gamma_ev_solar's routine (at 82% of optimal)")
    _seed_task(trading_db, "Trading: learn gamma_ev_solar's routine (at 90% of optimal)")
    _seed_task(trading_db, "Improve gamma_ev_solar model performance")
    fleet = _Fleet([_Cust("gamma_ev_solar", model="learned", has_solar=True, has_ev=True)])
    ctx = Ctx(conn=object(), fleet=fleet, cycle=1, agent="trading")

    notes = scripted.close_completed_trading_tasks(ctx)

    assert len(notes) == 3
    assert all(t["status"] == "done" for t in trading_db.tasks)
    # closed as an agent action, so the timeline finally credits the swarm
    assert all(p[-2] == "agent-trading" for (_sql, p) in trading_db.executed)


def test_sweep_leaves_homes_with_headroom_open(trading_db):
    """A solar+EV home still on 'naive' has real headroom; its task must stay open."""
    _seed_task(trading_db, "Trading: learn beta_solar_ev's routine")
    fleet = _Fleet([_Cust("beta_solar_ev", model="naive", has_solar=True, has_ev=True)])
    ctx = Ctx(conn=object(), fleet=fleet, cycle=1, agent="trading")

    notes = scripted.close_completed_trading_tasks(ctx)

    assert notes == []
    assert trading_db.tasks[0]["status"] == "todo"


def test_sweep_ignores_tasks_naming_no_settled_home(trading_db):
    """Generic tasks (no settled home in the title) are left for the brain to judge."""
    _seed_task(trading_db, "Investigate suboptimal customers")
    fleet = _Fleet([_Cust("alpha_stable", model="naive")])  # at best-fit, but unnamed
    ctx = Ctx(conn=object(), fleet=fleet, cycle=1, agent="trading")

    notes = scripted.close_completed_trading_tasks(ctx)

    assert notes == []
    assert trading_db.tasks[0]["status"] == "todo"
