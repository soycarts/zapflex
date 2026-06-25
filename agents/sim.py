"""Live sim engine: the heartbeat that makes the fleet move during the hands-off window.

Owns the in-memory fleet and advances it one sim day at a time. Each tick runs the
deterministic policy (plan on the forecast) and the oracle (perfect hindsight) for the
next day of real Agile prices, writes trades + benchmarks to Supabase, and rolls SOC.
The agents supervise this engine; they do not replace it.

The demonstrable autonomy win lives here: when the Trading agent learns a household's
routine it calls relearn(), which lifts that customer's forecast model
(naive -> seasonal -> learned). Subsequent ticks plan on the sharper forecast, the
captured savings rise toward the oracle, and the customer climbs the leaderboard live.
"""
from __future__ import annotations

import json
import random

import psycopg

from energy.household import generate_slots, stable_seed
from energy.forecast import forecast_series, MODELS
from energy.optimizer import run_window
from energy.policy import plan_and_settle
from agents import db

REGION = "C"
HORIZON_DAYS = 120          # cap precompute so memory/compute stay bounded
MODEL_LADDER = ["naive", "seasonal", "learned"]


def _iso(dt) -> str:
    return dt.isoformat()


class Customer:
    def __init__(self, row: dict, household: dict, battery: dict, actual_map: dict):
        self.id = row["id"]
        self.handle = row["handle"]
        self.region = row["region"]
        self.household = household
        self.battery = battery            # mutable dict of battery params (+ id)
        self.actual_map = actual_map      # {slot_iso: SlotSeries} over the horizon
        self.seed = stable_seed(self.handle)
        preset = battery.get("strategy_preset") or {}
        if isinstance(preset, str):
            preset = json.loads(preset)
        self.model = preset.get("forecast_model", "naive")
        self.version = 1

    @property
    def battery_id(self) -> int:
        return self.battery["id"]


class Fleet:
    """The live fleet: prices, customers, and the shared day cursor."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.days: list[str] = []
        self.day_slots: dict[str, list[str]] = {}
        self.import_p: dict[str, float] = {}
        self.export_p: dict[str, float] = {}
        self.customers: list[Customer] = []
        self.cursor = 0                   # index of the next day to reveal
        self._load_prices()

    # ---- prices -------------------------------------------------------------
    def _load_prices(self) -> None:
        rows = db.fetchall(
            self.conn,
            """
            select direction, slot_start, price_p_per_kwh
            from tariff_prices where region = %s order by slot_start
            """,
            (REGION,),
        )
        imp, exp = {}, {}
        for r in rows:
            s = _iso(r["slot_start"])
            if r["direction"] == "import":
                imp[s] = float(r["price_p_per_kwh"])
            else:
                exp[s] = float(r["price_p_per_kwh"])
        common = sorted(set(imp) & set(exp))
        self.import_p = {s: imp[s] for s in common}
        self.export_p = {s: exp[s] for s in common}
        day_slots: dict[str, list[str]] = {}
        for s in common:
            day_slots.setdefault(s[:10], []).append(s)
        # keep only full days (48 slots), bounded to the horizon
        full = [d for d in sorted(day_slots) if len(day_slots[d]) == 48][:HORIZON_DAYS]
        self.days = full
        self.day_slots = {d: day_slots[d] for d in full}

    @property
    def all_slots(self) -> list[str]:
        out: list[str] = []
        for d in self.days:
            out.extend(self.day_slots[d])
        return out

    # ---- fleet load ---------------------------------------------------------
    def load_customers(self) -> None:
        rows = db.fetchall(
            self.conn,
            """
            select c.id, c.handle, c.region,
                   h.annual_kwh, h.has_solar, h.solar_kwp, h.has_ev,
                   h.occupancy_profile, h.load_volatility,
                   b.id as battery_id, b.capacity_kwh, b.max_charge_kw, b.max_discharge_kw,
                   b.round_trip_eff, b.reserve_soc_pct, b.cycle_cap_per_day,
                   b.current_soc_kwh, b.strategy_preset
            from customers c
            join households h on h.customer_id = c.id
            join batteries  b on b.customer_id = c.id
            where c.status = 'active'
              and coalesce(c.acquisition_source, '') <> 'judge'
            order by c.id
            """,
        )
        known = {c.handle for c in self.customers}
        for r in rows:
            if r["handle"] in known:
                continue
            self._add_customer(r)

    def _add_customer(self, r: dict) -> Customer:
        household = {
            "annual_kwh": float(r["annual_kwh"]), "has_solar": r["has_solar"],
            "solar_kwp": float(r["solar_kwp"]), "has_ev": r["has_ev"],
            "occupancy_profile": r["occupancy_profile"],
            "load_volatility": float(r["load_volatility"]),
        }
        battery = {
            "id": r["battery_id"], "capacity_kwh": float(r["capacity_kwh"]),
            "max_charge_kw": float(r["max_charge_kw"]),
            "max_discharge_kw": float(r["max_discharge_kw"]),
            "round_trip_eff": float(r["round_trip_eff"]),
            "reserve_soc_pct": float(r["reserve_soc_pct"]),
            "cycle_cap_per_day": float(r["cycle_cap_per_day"]),
            "current_soc_kwh": float(r["current_soc_kwh"]),
            "strategy_preset": r["strategy_preset"],
        }
        seed = stable_seed(r["handle"])
        series = generate_slots(household, self.all_slots, seed=seed)
        actual_map = {s.slot_start: s for s in series["actual"]}
        cust = Customer(r, household, battery, actual_map)
        self.customers.append(cust)
        return cust

    # ---- the tick -----------------------------------------------------------
    def tick_day(self) -> dict | None:
        """Reveal the next sim day for every customer; write trades + benchmarks.

        Returns a small summary for the decisions_log, or None if out of days.
        """
        if self.cursor >= len(self.days):
            return None
        day = self.days[self.cursor]
        slots = self.day_slots[day]
        imp = [self.import_p[s] for s in slots]
        exp = [self.export_p[s] for s in slots]

        summary = {"sim_day": day, "customers": 0, "captured": 0.0, "optimal": 0.0}
        for cust in self.customers:
            res = self._tick_customer(cust, day, slots, imp, exp)
            summary["customers"] += 1
            summary["captured"] += res["captured"]
            summary["optimal"] += res["optimal"]
        summary["captured"] = round(summary["captured"], 4)
        summary["optimal"] = round(summary["optimal"], 4)
        self.cursor += 1
        return summary

    def _tick_customer(self, cust: Customer, day: str, slots, imp, exp) -> dict:
        bat = dict(cust.battery)
        cap = bat["capacity_kwh"]
        bat["current_soc_kwh"] = 0.5 * cap          # fresh, comparable start each day
        rte = bat["round_trip_eff"]

        fc = forecast_series(cust.household, slots, MODELS[cust.model], seed=cust.seed)
        actual = {s: cust.actual_map[s] for s in slots}

        captured, trades = plan_and_settle(bat, slots, imp, exp, fc, actual)

        oracle_in = [(s, i, e, actual[s].load_kwh, actual[s].solar_kwh)
                     for s, i, e in zip(slots, imp, exp)]
        optimal, _, bench = run_window(bat, oracle_in)
        b = bench[0]

        with self.conn.cursor() as cur:
            cur.executemany(
                """insert into trades (battery_id, customer_id, sim_time, action,
                       energy_kwh, price_p_per_kwh, cashflow, cycles_used)
                   values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(cust.battery_id, cust.id, t["sim_time"], t["action"], t["energy_kwh"],
                  t["price_p_per_kwh"], t["cashflow"], t["cycles_used"]) for t in trades],
            )
            cur.execute(
                """insert into benchmarks (customer_id, window_start, window_end,
                       optimal_savings, updated_at)
                   values (%s,%s,%s,%s, now())
                   on conflict (customer_id, window_start) do update
                     set optimal_savings = excluded.optimal_savings, updated_at = now()""",
                (cust.id, b["window_start"], b["window_end"], b["optimal_savings"]),
            )
            # roll SOC for the fleet mart (dispatchable energy reads current_soc)
            end_soc = 0.5 * cap
            for t in trades:
                if t["action"] == "charge":
                    end_soc += rte * t["energy_kwh"]
                elif t["action"] == "discharge":
                    end_soc -= t["energy_kwh"]
            end_soc = max(0.0, min(cap, end_soc))
            cust.battery["current_soc_kwh"] = end_soc
            cur.execute("update batteries set current_soc_kwh = %s where id = %s",
                        (end_soc, cust.battery_id))
        return {"captured": captured, "optimal": optimal}

    # ---- agent levers -------------------------------------------------------
    def get_customer(self, handle: str) -> Customer | None:
        for c in self.customers:
            if c.handle == handle:
                return c
        return None

    def relearn(self, handle: str, target: str | None = None, sim_time: str | None = None) -> dict:
        """Lift a customer's forecast model up the ladder (the autonomy win).

        Without an explicit target, advances one rung (naive -> seasonal -> learned).
        Writes a strategy_versions row and updates batteries.strategy_preset so the
        next tick plans on the sharper forecast.
        """
        cust = self.get_customer(handle)
        if not cust:
            return {"ok": False, "reason": f"no customer {handle}"}
        if target is None:
            i = MODEL_LADDER.index(cust.model) if cust.model in MODEL_LADDER else 0
            target = MODEL_LADDER[min(i + 1, len(MODEL_LADDER) - 1)]
        if target == cust.model:
            return {"ok": False, "reason": f"{handle} already on {target}"}
        prev = cust.model
        cust.model = target
        cust.version += 1
        preset = {"forecast_model": target, **MODELS[target]}
        with self.conn.cursor() as cur:
            cur.execute("update batteries set strategy_preset = %s where id = %s",
                        (json.dumps(preset), cust.battery_id))
            cur.execute(
                """insert into strategy_versions (battery_id, version, params, author_type, sim_time)
                   values (%s,%s,%s,'agent',%s)""",
                (cust.battery_id, cust.version, json.dumps(preset), sim_time),
            )
        return {"ok": True, "handle": handle, "from": prev, "to": target}

    def onboard(self, profile: dict) -> Customer:
        """Create a new sim customer (+ battery, household, connection) and add it live."""
        hh = profile["household"]
        bat = profile["battery"]
        model = profile.get("forecast_model", "naive")
        provider = profile.get("provider", "givenergy")
        err = profile.get("connection_error", False)
        with self.conn.cursor() as cur:
            cur.execute(
                """insert into customers (handle, region, import_tariff, export_tariff,
                       status, acquisition_source, sim_joined_at)
                   values (%s,%s,'AGILE','AGILE_OUTGOING','active',%s, now()) returning id""",
                (profile["handle"], REGION, profile.get("source", "growth_agent")),
            )
            cust_id = cur.fetchone()["id"]
            cur.execute(
                """insert into batteries (customer_id, brand, capacity_kwh, max_charge_kw,
                       max_discharge_kw, round_trip_eff, reserve_soc_pct, cycle_cap_per_day,
                       current_soc_kwh, strategy_preset)
                   values (%s,'SimBat',%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                (cust_id, bat["capacity_kwh"], bat["max_charge_kw"], bat["max_discharge_kw"],
                 bat.get("round_trip_eff", 0.90), bat.get("reserve_soc_pct", 0.10),
                 bat.get("cycle_cap_per_day", 1.5), bat["capacity_kwh"] * 0.5,
                 json.dumps({"forecast_model": model, **MODELS[model]})),
            )
            bat_id = cur.fetchone()["id"]
            cur.execute(
                """insert into households (customer_id, annual_kwh, has_solar, solar_kwp,
                       has_ev, occupancy_profile, load_volatility)
                   values (%s,%s,%s,%s,%s,%s,%s)""",
                (cust_id, hh["annual_kwh"], hh["has_solar"], hh["solar_kwp"], hh["has_ev"],
                 hh.get("occupancy_profile", "standard"), hh.get("load_volatility", 0.18)),
            )
            cur.execute(
                """insert into connections (customer_id, battery_id, provider, status,
                       error_reason, connected_at)
                   values (%s,%s,%s,%s,%s,%s)""",
                (cust_id, bat_id, provider,
                 "error" if err else "connected",
                 "telemetry handshake failed" if err else None,
                 None if err else db.now_iso()),
            )
        row = {
            "id": cust_id, "handle": profile["handle"], "region": REGION,
            "annual_kwh": hh["annual_kwh"], "has_solar": hh["has_solar"],
            "solar_kwp": hh["solar_kwp"], "has_ev": hh["has_ev"],
            "occupancy_profile": hh.get("occupancy_profile", "standard"),
            "load_volatility": hh.get("load_volatility", 0.18),
            "battery_id": bat_id, "capacity_kwh": bat["capacity_kwh"],
            "max_charge_kw": bat["max_charge_kw"], "max_discharge_kw": bat["max_discharge_kw"],
            "round_trip_eff": bat.get("round_trip_eff", 0.90),
            "reserve_soc_pct": bat.get("reserve_soc_pct", 0.10),
            "cycle_cap_per_day": bat.get("cycle_cap_per_day", 1.5),
            "current_soc_kwh": bat["capacity_kwh"] * 0.5,
            "strategy_preset": json.dumps({"forecast_model": model, **MODELS[model]}),
        }
        cust = self._add_customer(row)
        # backfill the new customer up to the current cursor so it appears on the board
        for d in range(self.cursor):
            day = self.days[d]
            slots = self.day_slots[day]
            self._tick_customer(cust, day, slots,
                                [self.import_p[s] for s in slots],
                                [self.export_p[s] for s in slots])
        return cust
