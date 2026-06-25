"""The harness: one loop that runs the whole company hands-off.

No agent framework. The loop checks the kill switch, advances the live sim, runs each
agent when its cadence is due (LLM-driven if an inference key is set, deterministic
otherwise), refreshes the dbt marts on a heartbeat, and logs every action to
decisions_log. Coordination is only through shared Supabase state and the tasks queue.

Usage:
    python -m agents.runner --reset            # fresh demo fleet, then run forever
    python -m agents.runner                     # continue the current state
    python -m agents.runner --reset --minutes 150
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

from agents import db, llm
from agents.sim import Fleet
from agents.tools import Ctx, ALLOWLIST, call
from agents import scripted

SOULS = Path(__file__).parent / "souls"

# Real-time cadences (seconds), compressed for the hands-off demo window.
CADENCE = {"sim": 3, "heartbeat": 12, "finance": 9, "trading": 15,
           "growth": 18, "support": 14, "ceo": 30}

# A stronger model for the CEO when LLM mode is on; the rest use the cheap default.
import os
CEO_MODEL = os.environ.get("INFERENCE_MODEL_CEO", os.environ.get("INFERENCE_MODEL", "openai/gpt-4o-mini"))
DEFAULT_MODEL = os.environ.get("INFERENCE_MODEL", "openai/gpt-4o-mini")

# Canonical demo fleet: the gamma_ev_solar climb (seasonal -> learned) is the headline.
SEED_FLEET = [
    {"handle": "alpha_stable", "forecast_model": "naive",
     "household": {"annual_kwh": 3500, "has_solar": False, "solar_kwp": 0.0, "has_ev": False, "load_volatility": 0.16},
     "battery": {"capacity_kwh": 10.0, "max_charge_kw": 3.6, "max_discharge_kw": 3.6}},
    {"handle": "beta_solar", "forecast_model": "naive",
     "household": {"annual_kwh": 4200, "has_solar": True, "solar_kwp": 4.5, "has_ev": False, "load_volatility": 0.20},
     "battery": {"capacity_kwh": 10.0, "max_charge_kw": 3.6, "max_discharge_kw": 3.6}},
    {"handle": "gamma_ev_solar", "forecast_model": "seasonal",
     "household": {"annual_kwh": 6000, "has_solar": True, "solar_kwp": 4.5, "has_ev": True, "load_volatility": 0.22},
     "battery": {"capacity_kwh": 13.5, "max_charge_kw": 5.0, "max_discharge_kw": 5.0}},
]

_TABLES_TO_RESET = [
    "benchmarks", "trades", "strategy_versions", "connections", "batteries",
    "households", "ledger", "support_tickets", "community_posts",
    "pending_approvals", "decisions_log", "customers",
]


def reset_state(conn) -> None:
    for t in _TABLES_TO_RESET:
        db.execute(conn, f"delete from {t}")
    db.execute(conn, "update kill_switch set halted=false, reason=null, updated_at=now() where id=1")
    print("  reset operational state")


def run_dbt(select: str = "staging marts", test: bool = False) -> bool:
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / "dbt"))
        import run_dbt as dbt_runner
        args = ["build" if test else "run", "--select", *select.split()]
        res = dbt_runner.run(args)
        return bool(getattr(res, "success", False))
    except Exception as e:
        print(f"  [heartbeat] dbt error: {e}")
        return False


def build_context(conn, fleet: Fleet, agent: str) -> str:
    lb = db.fetchall(conn, "select rank, handle, pct_of_optimal, captured_savings from mart_leaderboard order by rank")
    pnl = db.fetchone(conn, "select revenue_share, grid_services, costs, net, customer_count from mart_company_pnl where sim_day is null")
    sup = db.fetchone(conn, "select open_tickets, escalated from mart_support limit 1")
    recent = db.fetchall(conn, "select agent, action from decisions_log order by created_at desc limit 8")
    # The open tasks this agent owns (assigned to it) or could pick up (unassigned),
    # so it can mark its work done via update_task and avoid re-filing duplicates.
    my_tasks = db.fetchall(
        conn,
        """select id, title, status, category, priority, assigned_to, created_by_name
           from tasks
           where status in ('todo','doing')
             and (assigned_to = %s or assigned_to is null)
           order by case status when 'doing' then 0 else 1 end, priority, id
           limit 12""",
        (f"agent-{agent}",),
    )
    homes = [{"handle": c.handle, "model": c.model,
              "has_solar": c.household["has_solar"], "has_ev": c.household["has_ev"]}
             for c in fleet.customers]
    import json
    return (
        f"sim_day_index={fleet.cursor}/{len(fleet.days)}\n"
        f"leaderboard={json.dumps(lb, default=str)}\n"
        f"pnl={json.dumps(pnl, default=str)}\n"
        f"support={json.dumps(sup, default=str)}\n"
        f"fleet_homes={json.dumps(homes)}\n"
        f"open_tasks={json.dumps(my_tasks, default=str)}\n"
        f"recent_actions={json.dumps(recent, default=str)}\n"
    )


def run_agent(conn, fleet: Fleet, agent: str, cycle: int) -> None:
    ctx = Ctx(conn=conn, fleet=fleet, cycle=cycle, agent=agent)
    sim_time = scripted._sim_time(fleet)
    try:
        if llm.enabled():
            try:
                soul = (SOULS / f"{agent}.md").read_text()
                model = CEO_MODEL if agent == "ceo" else DEFAULT_MODEL
                ctx_text = build_context(conn, fleet, agent)
                plan = llm.decide(soul, model, ctx_text, ALLOWLIST[agent])
                results, approvals, tasks = [], [], []
                for act in plan.get("actions", [])[:3]:
                    name = act.get("tool")
                    args = act.get("args", {}) or {}
                    if name not in ALLOWLIST[agent]:
                        continue
                    if name in ("learn_routine", "set_forecast_model", "book_cost"):
                        args.setdefault("sim_time", sim_time)
                    r = call(ctx, name)(ctx, **args)
                    results.append(r.get("note", name))
                    if r.get("approval"):
                        approvals.append(r["approval"])
                    if r.get("task_id"):
                        tasks.append(r["task_id"])
                # Deterministic safety net: the LLM often forgets to close finished
                # work, so sweep the board for Trading tasks whose home is now at its
                # best-fit forecast. Keeps the board a clean lifecycle, not a todo flood.
                if agent == "trading":
                    results.extend(scripted.close_completed_trading_tasks(ctx))
                db.log_decision(conn, agent, action="llm cycle",
                                rationale=plan.get("rationale", "") + " | " + "; ".join(results),
                                cycle=cycle, sim_time=sim_time,
                                approvals_requested=approvals or None, tasks_created=tasks or None)
                print(f"  [{agent}] {plan.get('rationale','')[:80]}")
                return
            except Exception as e:
                print(f"  [{agent}] LLM failed ({e}); using scripted policy")
        out = scripted.AGENTS[agent](ctx)
        rationale = out.get("rationale", "")
        notes = "; ".join(out.get("results", []))
        db.log_decision(conn, agent, action=out.get("action", "cycle"),
                        rationale=(rationale + (" | " + notes if notes else "")),
                        cycle=cycle, sim_time=sim_time,
                        state_summary=out.get("state_summary"),
                        tasks_created=out.get("tasks_created"))
        print(f"  [{agent}] {out.get('action','')}: {rationale[:70]}")
    except Exception:
        err = traceback.format_exc().splitlines()[-1]
        print(f"  [{agent}] ERROR: {err}")
        try:
            db.log_decision(conn, agent, action="error", rationale=err, cycle=cycle, sim_time=sim_time)
        except Exception:
            pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="wipe operational state and seed a fresh demo fleet")
    ap.add_argument("--minutes", type=float, default=0, help="auto-stop after N minutes (0 = run forever)")
    ap.add_argument("--seed-days", type=int, default=3, help="sim days to pre-tick on reset so the board starts populated")
    args = ap.parse_args()

    conn = db.connect()
    print(f"zapflex swarm starting. inference={'LLM ('+DEFAULT_MODEL+')' if llm.enabled() else 'scripted'}")

    if args.reset:
        print("Resetting...")
        reset_state(conn)

    fleet = Fleet(conn)
    if not fleet.days:
        sys.exit("No tariff_prices found. Seed prices first (energy/ingest_agile.py).")

    if args.reset:
        print("Seeding demo fleet...")
        for spec in SEED_FLEET:
            fleet.onboard({**spec, "battery": {**spec["battery"]}, "provider": "givenergy",
                           "connection_error": False, "source": "sim_seed"})
            print(f"  seeded {spec['handle']} on {spec['forecast_model']}")
        for _ in range(max(0, args.seed_days)):
            fleet.tick_day()
        print(f"  pre-ticked {args.seed_days} sim days")
    else:
        fleet.load_customers()
        # align cursor to days already simulated
        row = db.fetchone(conn, "select count(distinct window_start) n from benchmarks")
        fleet.cursor = min(int(row["n"]) if row else 0, len(fleet.days))
    print(f"fleet: {len(fleet.customers)} homes, cursor day {fleet.cursor}/{len(fleet.days)}")

    print("Initial dbt build...")
    run_dbt(test=False)

    last = {k: 0.0 for k in CADENCE}
    cyc = {a: 0 for a in ALLOWLIST}
    start = time.time()
    halted_logged = False

    while True:
        if args.minutes and (time.time() - start) > args.minutes * 60:
            print("Reached --minutes limit; stopping.")
            break
        now = time.time()

        if db.kill_switch_engaged(conn):
            if not halted_logged:
                print("KILL SWITCH ENGAGED — agents paused. Clear kill_switch to resume.")
                halted_logged = True
            time.sleep(2)
            continue
        halted_logged = False

        if now - last["sim"] >= CADENCE["sim"]:
            last["sim"] = now
            summary = fleet.tick_day()
            if summary:
                db.log_decision(conn, "sim", action="tick", sim_time=summary["sim_day"],
                                rationale=f"replayed {summary['sim_day']}: {summary['customers']} homes, "
                                          f"captured £{summary['captured']:.2f} of £{summary['optimal']:.2f} optimal",
                                state_summary=summary)
                print(f"[sim] {summary['sim_day']} captured £{summary['captured']:.2f}/£{summary['optimal']:.2f}")

        for agent in ("finance", "trading", "growth", "support", "ceo"):
            if now - last[agent] >= CADENCE[agent]:
                last[agent] = now
                cyc[agent] += 1
                run_agent(conn, fleet, agent, cyc[agent])

        if now - last["heartbeat"] >= CADENCE["heartbeat"]:
            last["heartbeat"] = now
            ok = run_dbt(test=False)
            print(f"[heartbeat] marts {'refreshed' if ok else 'FAILED'}")

        time.sleep(0.5)

    conn.close()


if __name__ == "__main__":
    main()
