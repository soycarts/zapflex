"""Deterministic agent policies: the swarm's reliable baseline behaviour.

Each function is one agent's decision step. It reads shared Supabase state, takes a
consequential action through the scoped tool registry, and returns a summary the
runner writes to decisions_log. This path needs no inference key, so the company
keeps running on rules alone; when an inference key is present the LLM decider
(agents/llm.py) drives the same tools and falls back to these on any error.
"""
from __future__ import annotations

import os
import random

from agents import db
from agents.tools import Ctx, call

_rng = random.Random()

# Plausible household archetypes for Growth to acquire. Most join below their
# ceiling so the Trading agent has routines left to learn.
_ARCHETYPES = [
    {"tag": "stable",     "household": {"annual_kwh": 3200, "has_solar": False, "solar_kwp": 0.0, "has_ev": False, "load_volatility": 0.16}, "model": "naive"},
    {"tag": "solar",      "household": {"annual_kwh": 4200, "has_solar": True,  "solar_kwp": 4.5, "has_ev": False, "load_volatility": 0.20}, "model": "naive"},
    {"tag": "ev",         "household": {"annual_kwh": 5200, "has_solar": False, "solar_kwp": 0.0, "has_ev": True,  "load_volatility": 0.22}, "model": "naive"},
    {"tag": "solar_ev",   "household": {"annual_kwh": 6000, "has_solar": True,  "solar_kwp": 5.0, "has_ev": True,  "load_volatility": 0.22}, "model": "seasonal"},
]
_BAT = {"capacity_kwh": 10.0, "max_charge_kw": 3.6, "max_discharge_kw": 3.6,
        "round_trip_eff": 0.90, "reserve_soc_pct": 0.10, "cycle_cap_per_day": 1.5}
_PROVIDERS = ["givenergy", "tesla", "sigenergy", "solis"]


def _sim_time(fleet) -> str | None:
    if fleet.days and fleet.cursor > 0:
        return fleet.day_slots[fleet.days[fleet.cursor - 1]][0]
    return None


# ---- CEO ----------------------------------------------------------------------
def ceo(ctx: Ctx) -> dict:
    lb = db.fetchall(ctx.conn, "select handle, pct_of_optimal, rank from mart_leaderboard order by rank")
    pnl = db.fetchone(ctx.conn, "select net, customer_count from mart_company_pnl where sim_day is null")
    net = float(pnl["net"]) if pnl and pnl["net"] is not None else 0.0
    n = pnl["customer_count"] if pnl else 0
    results, tasks = [], []

    # Direct Trading at the biggest skill gap by filing a task.
    laggards = [r for r in lb if r["pct_of_optimal"] is not None and float(r["pct_of_optimal"]) < 0.92]
    if laggards:
        worst = min(laggards, key=lambda r: float(r["pct_of_optimal"]))
        r = call(ctx, "create_task")(ctx,
            title=f"Trading: learn {worst['handle']}'s routine (at {float(worst['pct_of_optimal'])*100:.0f}% of optimal)",
            category="trading", priority=2, assigned_to="trading")
        results.append(r["note"]); tasks.append(r.get("task_id"))

    # Once the fleet and P&L justify it, propose the licensing move to the human gate.
    if net >= 5.0 and n >= 5:
        already = db.fetchone(ctx.conn,
            "select 1 from pending_approvals where action_type='licensing' limit 1")
        if not already:
            r = call(ctx, "request_approval")(ctx, action_type="licensing",
                summary=f"Apply for a VLP licence to unlock Balancing Mechanism revenue on {n} enrolled homes (£{net:.2f}/sim-period booked behind-the-meter).",
                payload={"move": "vlp_application", "fleet": n, "net_gbp": net})
            results.append(r["note"])

    rationale = f"Fleet {n} homes, net £{net:.2f}. " + (
        "Directing Trading at the weakest forecast." if laggards else "Fleet near optimal; holding course.")
    return {"action": "strategy review", "rationale": rationale,
            "state_summary": {"net_gbp": net, "customers": n,
                              "top": lb[0]["handle"] if lb else None},
            "results": results, "tasks_created": tasks or None}


# ---- Trading ------------------------------------------------------------------
def _best_fit(household: dict) -> str:
    """The highest forecast model that still earns its keep for this household.
    EV homes top out at 'learned', solar-only at 'seasonal', the rest at 'naive'.
    Lifting past best-fit predicts a routine the home lacks and loses yield, so
    a home sitting on its best-fit has no Trading headroom left."""
    if household.get("has_ev"):
        return "learned"
    if household.get("has_solar"):
        return "seasonal"
    return "naive"


def close_completed_trading_tasks(ctx: Ctx) -> list[str]:
    """Sweep the board and close every open Trading task whose named home has
    already reached its best-fit forecast model — the work is done, so it should
    not linger as a stale todo. Runs every cycle, in both LLM and scripted mode,
    independent of whether a home was advanced this cycle. This is the deterministic
    safety net that stops Trading tasks piling up when the brain forgets to close them.

    Closes ALL matching tasks (the CEO files near-duplicate tasks per home), not
    just one. Generic tasks naming no settled home are left for the brain to judge."""
    settled = [c for c in ctx.fleet.customers if _best_fit(c.household) == c.model]
    if not settled:
        return []
    open_tasks = db.fetchall(ctx.conn,
        """select id, title from tasks where status in ('todo','doing')
               and assigned_to='agent-trading' order by id""")
    notes = []
    for t in open_tasks:
        title = t["title"].lower()
        home = next((c for c in settled if c.handle.lower() in title), None)
        if home:
            ur = call(ctx, "update_task")(ctx, task_id=t["id"], status="done",
                result=f"{home.handle} at best-fit forecast ('{home.model}'); no further headroom")
            notes.append(ur["note"])
    return notes


def trading(ctx: Ctx) -> dict:
    """Learn one home's routine per cycle, advancing its forecast model where it helps,
    then sweep the board so completed/stale Trading tasks are closed, not piled up."""
    candidates = []
    for c in ctx.fleet.customers:
        hh = c.household
        target = _best_fit(hh)
        if target != c.model:
            candidates.append((c.handle, target))
    if not candidates:
        closed = close_completed_trading_tasks(ctx)
        return {"action": "supervise",
                "rationale": "Every home is on its best-fit forecast; monitoring settlement.",
                "results": closed or ["fleet at best-fit; board clear"]}
    # Prefer the lowest current pct_of_optimal so the visible climb is the laggard's.
    pcts = {r["handle"]: (float(r["pct_of_optimal"]) if r["pct_of_optimal"] is not None else 1.0)
            for r in db.fetchall(ctx.conn, "select handle, pct_of_optimal from mart_leaderboard")}
    candidates.sort(key=lambda x: pcts.get(x[0], 1.0))
    handle, target = candidates[0]
    r = call(ctx, "set_forecast_model")(ctx, handle=handle, model=target, sim_time=_sim_time(ctx.fleet))
    results = [r["note"]]

    # Close the loop on the board: the home just advanced is now at best-fit, and
    # any other home already at best-fit gets its lingering tasks closed too.
    results.extend(close_completed_trading_tasks(ctx))
    return {"action": "learned routine",
            "rationale": f"Watched {handle}'s settled load converge on a routine; lifting its forecast to '{target}' to capture the headroom.",
            "results": results,
            "state_summary": {"customer": handle, "model": target}}


# ---- Finance ------------------------------------------------------------------
def finance(ctx: Ctx) -> dict:
    results = []
    r = call(ctx, "book_revenue")(ctx)
    results.append(r["note"])

    # Close the gate loop: book any human-approved spend not yet in the ledger.
    approved = db.fetchall(ctx.conn,
        """select id, payload from pending_approvals
           where action_type='spend' and status='approved'""")
    for a in approved:
        marker = f"approval:#{a['id']}"
        seen = db.fetchone(ctx.conn, "select 1 from ledger where note like %s", (f"%{marker}%",))
        if seen:
            continue
        payload = a["payload"] or {}
        amount = float(payload.get("amount_gbp", payload.get("amount", 0)) or 0)
        if amount > 0:
            br = call(ctx, "book_cost")(ctx, amount_gbp=amount,
                                        note=f"{payload.get('purpose','approved spend')} ({marker})",
                                        sim_time=_sim_time(ctx.fleet))
            results.append(br["note"])

    # Compliance: trip the kill switch if any battery breaches its cycle cap on a day.
    breach = db.fetchone(ctx.conn,
        """select t.battery_id, date(t.sim_time) d, sum(t.cycles_used) cyc, b.cycle_cap_per_day cap
           from trades t join batteries b on b.id=t.battery_id
           group by t.battery_id, date(t.sim_time), b.cycle_cap_per_day
           having sum(t.cycles_used) > b.cycle_cap_per_day + 0.01 limit 1""")
    if breach:
        kr = call(ctx, "trip_kill_switch")(ctx,
            reason=f"battery {breach['battery_id']} exceeded cycle cap ({float(breach['cyc']):.2f}>{float(breach['cap'])}) on {breach['d']}")
        results.append(kr["note"])

    pnl = db.fetchone(ctx.conn, "select revenue_share, grid_services, net from mart_company_pnl where sim_day is null")
    net = float(pnl["net"]) if pnl and pnl["net"] is not None else 0.0
    return {"action": "book P&L",
            "rationale": f"Settled the ledger to date; company net £{net:.2f}. Caps holding." ,
            "results": results, "state_summary": {"net_gbp": net}}


# ---- Growth -------------------------------------------------------------------
def growth(ctx: Ctx) -> dict:
    results = []
    fleet_max = int(os.environ.get("DEMO_FLEET_MAX", "999"))
    if len(ctx.fleet.customers) >= fleet_max:
        return {
            "action": "fleet cap",
            "rationale": f"At demo fleet cap ({fleet_max}); skipping acquisition.",
            "results": [],
            "state_summary": {"customers": len(ctx.fleet.customers), "cap": fleet_max},
        }
    # Acquire a new home most cycles.
    arch = _rng.choice(_ARCHETYPES)
    n_existing = len(ctx.fleet.customers)
    handle = f"{arch['tag']}_{n_existing+1:02d}_{_rng.randint(100,999)}"
    hh = dict(arch["household"])
    profile = {
        "handle": handle, "household": hh, "battery": dict(_BAT),
        "forecast_model": arch["model"], "provider": _rng.choice(_PROVIDERS),
        "connection_error": _rng.random() < 0.25, "source": "growth_agent",
    }
    r = call(ctx, "onboard_customer")(ctx, profile=profile)
    results.append(r["note"])

    # Periodically post a leaderboard update to the community.
    if ctx.cycle % 2 == 0:
        top = db.fetchone(ctx.conn, "select handle, pct_of_optimal from mart_leaderboard order by rank limit 1")
        if top and top["pct_of_optimal"] is not None:
            pr = call(ctx, "post_community")(ctx, author_name="growth",
                body=f"📈 {top['handle']} leads the skill board at {float(top['pct_of_optimal'])*100:.1f}% of optimal. Tune your forecast and climb!")
            results.append(pr["note"])

    # Occasionally request budget for an acquisition push (gated spend).
    if ctx.cycle == 3:
        pending = db.fetchone(ctx.conn,
            "select 1 from pending_approvals where action_type='spend' limit 1")
        if not pending:
            ar = call(ctx, "request_approval")(ctx, action_type="spend",
                summary="£25 referral-credit campaign to acquire ~5 flexible homes.",
                payload={"amount_gbp": 25, "purpose": "referral acquisition campaign"})
            results.append(ar["note"])

    return {"action": "acquire + engage",
            "rationale": f"Onboarded {handle}; nudging the community around the leaderboard.",
            "results": results, "state_summary": {"new_customer": handle}}


# ---- Support ------------------------------------------------------------------
_TICKET_QS = [
    ("How do I climb the leaderboard?", "Your rank is forecast skill: pct of the perfect-hindsight optimum. Let our agent learn your EV/solar routine — it lifts your forecast model and your captured savings follow."),
    ("Why did my rank change overnight?", "We re-learned a few homes' routines, which shifts relative ranks. Your absolute savings only went up — the board is normalised by hardware so it's a fair skill measure."),
    ("Is my battery safe from over-cycling?", "Yes — a hard cycle cap is enforced in both the optimiser and the oracle, and Finance trips a kill switch on any breach. We protect the asset above short-term yield."),
]


def support(ctx: Ctx) -> dict:
    results = []
    # Recover connection errors raised by onboarding (cross-agent coordination).
    errs = db.fetchall(ctx.conn,
        "select id, customer_id from connections where status='error' order by id limit 2")
    for e in errs:
        if _rng.random() < 0.8:
            rr = call(ctx, "resolve_connection")(ctx, connection_id=e["id"])
            results.append(rr["note"])
        else:
            tr = call(ctx, "open_ticket")(ctx, customer_id=e["customer_id"],
                subject="Connection handshake failing", body="Battery telemetry not arriving after onboarding.",
                priority=2)
            call(ctx, "escalate_ticket")(ctx, ticket_id=tr["ticket_id"], reason="provider API not responding")
            results.append(tr["note"] + " (escalated)")

    # Answer any open tickets.
    open_t = db.fetchall(ctx.conn,
        "select id, subject from support_tickets where status='open' order by priority, id limit 3")
    for t in open_t:
        ans = next((a for q, a in _TICKET_QS if q == t["subject"]),
                   "Thanks for reaching out — our agent has reviewed your account and applied the fix.")
        call(ctx, "answer_ticket")(ctx, ticket_id=t["id"], resolution=ans)
        results.append(f"answered ticket #{t['id']}")

    # Simulate an inbound community question every couple of cycles, then answer it.
    if ctx.cycle % 2 == 1:
        cust = db.fetchone(ctx.conn,
            """select id from customers where status='active'
               and coalesce(acquisition_source,'')<>'judge' order by random() limit 1""")
        if cust:
            q, a = _rng.choice(_TICKET_QS)
            tr = call(ctx, "open_ticket")(ctx, customer_id=cust["id"], subject=q, body=q, priority=3)
            call(ctx, "answer_ticket")(ctx, ticket_id=tr["ticket_id"], resolution=a)
            results.append(f"fielded + answered: {q[:40]}")

    if not results:
        results.append("inbox clear; no open tickets or connection errors")
    return {"action": "support sweep",
            "rationale": "Cleared connection errors and answered the community queue.",
            "results": results}


AGENTS = {"ceo": ceo, "trading": trading, "finance": finance, "growth": growth, "support": support}
