"""The narrow tool registry. Each agent is scoped blank-slate to its own allowlist.

Tools are plain functions taking a Ctx (live connection, the sim fleet, the cycle
number, the agent name) plus keyword args, and returning a result dict that always
carries a human-readable "note" for the decisions_log. The runner exposes only an
agent's allowlisted tools, whether it is driven by the LLM or the scripted policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agents import db, gate
from agents.sim import Fleet

GRID_SERVICES_GBP_PER_KW_YEAR = 100.0
REVENUE_SHARE_PCT = 0.20


def actor_name(agent: str) -> str:
    """Tasks-board label for a run-time agent: `agent-<type>` (e.g. agent-ceo).
    The actor *type* stays 'agent' for timeline colouring; the *name* says which one."""
    return f"agent-{agent}"


@dataclass
class Ctx:
    conn: Any
    fleet: Fleet
    cycle: int
    agent: str


# --- CEO / shared --------------------------------------------------------------
def create_task(ctx: Ctx, title: str, category: str = "ops", priority: int = 3,
                assigned_to: str | None = None) -> dict:
    """File a task. When one agent directs another (e.g. CEO tasking Trading),
    pass assigned_to as that agent's name so the board shows the owner, not just
    the author."""
    owner = actor_name(assigned_to) if assigned_to else None
    row = db.fetchone(
        ctx.conn,
        """insert into tasks (title, phase, category, status, priority,
               created_by_type, created_by_name, assigned_to)
           values (%s,'live',%s,'todo',%s,'agent',%s,%s) returning id""",
        (title, category, priority, actor_name(ctx.agent), owner),
    )
    return {"note": f"created task #{row['id']}: {title}", "task_id": row["id"]}


def update_task(ctx: Ctx, task_id: int, status: str, result: str = "") -> dict:
    db.execute(
        ctx.conn,
        """update tasks set status=%s, result=coalesce(nullif(%s,''), result),
               completed_at = case when %s in ('done','cancelled') then now() else completed_at end,
               completed_by_type = case when %s in ('done','cancelled') then 'agent' else completed_by_type end,
               completed_by_name = case when %s in ('done','cancelled') then %s else completed_by_name end,
               started_at = coalesce(started_at, now())
           where id=%s""",
        (status, result, status, status, status, actor_name(ctx.agent), task_id),
    )
    return {"note": f"task #{task_id} -> {status}"}


def request_approval(ctx: Ctx, action_type: str, summary: str, payload: dict | None = None) -> dict:
    res = gate.request_approval(ctx.conn, ctx.agent, action_type, payload or {"summary": summary}, summary)
    note = f"requested {action_type} approval (#{res['approval_id']}): {summary}"
    return {"note": note, "approval": {"id": res["approval_id"], "type": action_type,
                                       "notified": res["notified"]}}


# --- Trading -------------------------------------------------------------------
def learn_routine(ctx: Ctx, handle: str, sim_time: str | None = None) -> dict:
    res = ctx.fleet.relearn(handle, sim_time=sim_time)
    if not res.get("ok"):
        return {"note": f"no change for {handle}: {res.get('reason')}", **res}
    return {"note": f"learned {handle}'s routine: forecast {res['from']} -> {res['to']}", **res}


def set_forecast_model(ctx: Ctx, handle: str, model: str, sim_time: str | None = None) -> dict:
    res = ctx.fleet.relearn(handle, target=model, sim_time=sim_time)
    return {"note": f"set {handle} forecast model -> {model}", **res}


# --- Finance -------------------------------------------------------------------
def book_revenue(ctx: Ctx) -> dict:
    """Book revenue_share (per customer) and grid_services (company) for any sim days
    that have trades but are not yet in the ledger. Drives mart_company_pnl."""
    days = db.fetchall(
        ctx.conn,
        """select distinct date(sim_time) as d from trades
           where date(sim_time) not in (
               select distinct date(sim_time) from ledger where entry_type='revenue_share')
           order by d""",
    )
    if not days:
        return {"note": "ledger already up to date"}
    flex = db.fetchone(
        ctx.conn,
        """select coalesce(sum(b.max_discharge_kw),0) as kw
           from batteries b join customers c on c.id=b.customer_id
           where c.status='active' and coalesce(c.acquisition_source,'')<>'judge'""",
    )
    grid_per_day = float(flex["kw"]) * GRID_SERVICES_GBP_PER_KW_YEAR / 365.0
    booked, total = 0, 0.0
    for row in days:
        d = row["d"].isoformat()
        per_cust = db.fetchall(
            ctx.conn,
            """select customer_id, sum(cashflow) as saving from trades
               where date(sim_time)=%s group by customer_id""",
            (d,),
        )
        for pc in per_cust:
            saving = float(pc["saving"])
            if saving <= 0:
                continue
            amt = round(REVENUE_SHARE_PCT * saving, 6)
            total += amt
            db.execute(
                ctx.conn,
                """insert into ledger (customer_id, sim_time, entry_type, amount, note)
                   values (%s,%s,'revenue_share',%s,%s)""",
                (pc["customer_id"], d, amt, f"{int(REVENUE_SHARE_PCT*100)}% of customer saving"),
            )
        db.execute(
            ctx.conn,
            """insert into ledger (customer_id, sim_time, entry_type, amount, note)
               values (null,%s,'grid_services',%s,%s)""",
            (d, round(grid_per_day, 6), "flexibility availability payment"),
        )
        total += grid_per_day
        booked += 1
    return {"note": f"booked {booked} sim day(s): +£{total:.2f} revenue + grid services"}


def trip_kill_switch(ctx: Ctx, reason: str) -> dict:
    db.execute(ctx.conn,
               "update kill_switch set halted=true, reason=%s, updated_at=now() where id=1",
               (reason,))
    return {"note": f"KILL SWITCH ENGAGED: {reason}"}


def book_cost(ctx: Ctx, amount_gbp: float, note: str, sim_time: str | None = None) -> dict:
    """Book an approved spend as a negative ledger entry (cost). Gate it before calling."""
    db.execute(
        ctx.conn,
        """insert into ledger (customer_id, sim_time, entry_type, amount, note)
           values (null, coalesce(%s, now()), 'cost', %s, %s)""",
        (sim_time, -abs(amount_gbp), note),
    )
    return {"note": f"booked cost -£{abs(amount_gbp):.2f}: {note}"}


# --- Growth --------------------------------------------------------------------
def onboard_customer(ctx: Ctx, profile: dict) -> dict:
    cust = ctx.fleet.onboard(profile)
    hh = profile["household"]
    tags = []
    if hh.get("has_solar"):
        tags.append("solar")
    if hh.get("has_ev"):
        tags.append("EV")
    desc = "+".join(tags) if tags else "stable"
    return {"note": f"onboarded {cust.handle} ({desc}) on {profile.get('forecast_model','naive')}",
            "customer_id": cust.id, "handle": cust.handle}


def post_community(ctx: Ctx, body: str, author_name: str | None = None) -> dict:
    db.execute(
        ctx.conn,
        """insert into community_posts (customer_id, author_type, author_name, channel, body)
           values (null, 'agent', %s, 'discord', %s)""",
        (author_name or ctx.agent, body),
    )
    return {"note": f"posted to community: {body[:60]}"}


# --- Support -------------------------------------------------------------------
def open_ticket(ctx: Ctx, customer_id: int | None, subject: str, body: str, priority: int = 3) -> dict:
    row = db.fetchone(
        ctx.conn,
        """insert into support_tickets (customer_id, channel, subject, body, status, priority)
           values (%s,'discord',%s,%s,'open',%s) returning id""",
        (customer_id, subject, body, priority),
    )
    return {"note": f"ticket #{row['id']} opened: {subject}", "ticket_id": row["id"]}


def answer_ticket(ctx: Ctx, ticket_id: int, resolution: str) -> dict:
    db.execute(
        ctx.conn,
        """update support_tickets set status='answered', answered_by=%s, resolution=%s,
               answered_at=now() where id=%s""",
        (ctx.agent, resolution, ticket_id),
    )
    return {"note": f"answered ticket #{ticket_id}"}


def escalate_ticket(ctx: Ctx, ticket_id: int, reason: str) -> dict:
    db.execute(ctx.conn, "update support_tickets set status='escalated' where id=%s", (ticket_id,))
    return {"note": f"escalated ticket #{ticket_id}: {reason}"}


def resolve_connection(ctx: Ctx, connection_id: int) -> dict:
    db.execute(
        ctx.conn,
        """update connections set status='connected', error_reason=null,
               connected_at=now(), last_telemetry_at=now() where id=%s""",
        (connection_id,),
    )
    return {"note": f"recovered connection #{connection_id}"}


# --- registry & allowlists -----------------------------------------------------
TOOLS: dict[str, Callable[..., dict]] = {
    "create_task": create_task,
    "update_task": update_task,
    "request_approval": request_approval,
    "learn_routine": learn_routine,
    "set_forecast_model": set_forecast_model,
    "book_revenue": book_revenue,
    "book_cost": book_cost,
    "trip_kill_switch": trip_kill_switch,
    "onboard_customer": onboard_customer,
    "post_community": post_community,
    "open_ticket": open_ticket,
    "answer_ticket": answer_ticket,
    "escalate_ticket": escalate_ticket,
    "resolve_connection": resolve_connection,
}

ALLOWLIST: dict[str, set[str]] = {
    "ceo":     {"create_task", "update_task", "request_approval"},
    "trading": {"learn_routine", "set_forecast_model", "create_task"},
    "finance": {"book_revenue", "book_cost", "trip_kill_switch", "request_approval"},
    "growth":  {"onboard_customer", "post_community", "request_approval", "create_task"},
    "support": {"answer_ticket", "escalate_ticket", "resolve_connection", "post_community", "open_ticket"},
}


def call(ctx: Ctx, name: str) -> Callable[..., dict]:
    if name not in ALLOWLIST.get(ctx.agent, set()):
        raise PermissionError(f"agent '{ctx.agent}' not allowed tool '{name}'")
    return TOOLS[name]
