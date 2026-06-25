"""Live sim feed: seed customers, run policy + oracle, write trades and benchmarks to Supabase.

Usage:
    python3 -m energy.sim_feed [--days N] [--reset]

--days N    How many days of Agile prices to replay (default: 7).
--reset     Wipe existing sim data (customers, batteries, trades, benchmarks) before run.
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import duckdb
import psycopg
from dotenv import load_dotenv

from energy.household import generate_slots, stable_seed
from energy.forecast import forecast_series, MODELS
from energy.optimizer import run_window
from energy.policy import plan_and_settle

load_dotenv()
DUCKDB_PATH = Path(__file__).parent.parent / "data" / "agile.duckdb"

# Three sim customers. The lever is the forecast model each one runs: stable needs
# nothing beyond the naive profile, solar benefits from the seasonal climatology, and
# the EV home only reaches its ceiling once the agent has learned its charging routine.
# gamma starts on 'seasonal' (EV routine NOT yet learned) so the trading agent has a
# real, demonstrable win: learn the routine, switch gamma to 'learned', climb.
SIM_CUSTOMERS = [
    {
        "handle": "alpha_stable",
        "household": {
            "annual_kwh": 3500, "has_solar": False, "solar_kwp": 0.0,
            "has_ev": False, "occupancy_profile": "standard", "load_volatility": 0.16,
        },
        "battery": {
            "capacity_kwh": 10.0, "max_charge_kw": 3.6, "max_discharge_kw": 3.6,
            "round_trip_eff": 0.90, "reserve_soc_pct": 0.10, "cycle_cap_per_day": 1.5,
        },
        "forecast_model": "naive",
    },
    {
        "handle": "beta_solar",
        "household": {
            "annual_kwh": 4000, "has_solar": True, "solar_kwp": 4.0,
            "has_ev": False, "occupancy_profile": "standard", "load_volatility": 0.20,
        },
        "battery": {
            "capacity_kwh": 10.0, "max_charge_kw": 3.6, "max_discharge_kw": 3.6,
            "round_trip_eff": 0.90, "reserve_soc_pct": 0.10, "cycle_cap_per_day": 1.5,
        },
        "forecast_model": "seasonal",
    },
    {
        "handle": "gamma_ev_solar",
        "household": {
            "annual_kwh": 5500, "has_solar": True, "solar_kwp": 4.0,
            "has_ev": True, "occupancy_profile": "standard", "load_volatility": 0.20,
        },
        "battery": {
            "capacity_kwh": 10.0, "max_charge_kw": 3.6, "max_discharge_kw": 3.6,
            "round_trip_eff": 0.90, "reserve_soc_pct": 0.10, "cycle_cap_per_day": 1.5,
        },
        "forecast_model": "seasonal",
    },
]


def _load_prices(conn_ddb: duckdb.DuckDBPyConnection, days: int) -> tuple[list[str], list[float], list[float]]:
    day_rows = conn_ddb.execute("""
        SELECT DISTINCT slot_start::date AS day
        FROM tariff_prices WHERE direction = 'import'
          AND slot_start::date IN (
              SELECT DISTINCT slot_start::date FROM tariff_prices WHERE direction = 'export')
        ORDER BY day DESC LIMIT ?
    """, [days]).fetchall()
    if not day_rows:
        raise RuntimeError("No price data. Run energy/ingest_agile.py first.")
    day_list = sorted(d[0].strftime("%Y-%m-%d") for d in day_rows)

    def fetch(direction: str) -> list[tuple]:
        return conn_ddb.execute("""
            SELECT slot_start::text, price_p_per_kwh
            FROM tariff_prices WHERE direction=?
              AND slot_start::date >= ? AND slot_start::date <= ?
            ORDER BY slot_start
        """, [direction, day_list[0], day_list[-1]]).fetchall()

    imp = fetch("import")
    exp = fetch("export")
    imp_map = {r[0]: float(r[1]) for r in imp}
    exp_map = {r[0]: float(r[1]) for r in exp}
    common = sorted(set(imp_map) & set(exp_map))
    return common, [imp_map[s] for s in common], [exp_map[s] for s in common]


def _reset(cur: psycopg.Cursor) -> None:
    cur.execute("DELETE FROM benchmarks")
    cur.execute("DELETE FROM trades")
    cur.execute("DELETE FROM strategy_versions")
    cur.execute("DELETE FROM connections")
    cur.execute("DELETE FROM batteries")
    cur.execute("DELETE FROM households")
    cur.execute("DELETE FROM customers")
    print("  Cleared sim data.")


def _seed_customers(cur: psycopg.Cursor) -> list[dict]:
    """Insert customers, batteries, households; return list of seeded records with DB ids."""
    seeded = []
    for spec in SIM_CUSTOMERS:
        cur.execute("""
            INSERT INTO customers (handle, region, import_tariff, export_tariff, status,
                                   acquisition_source, sim_joined_at)
            VALUES (%s, 'C', 'AGILE', 'AGILE_OUTGOING', 'active', 'sim_seed', now())
            RETURNING id
        """, [spec["handle"]])
        cust_id = cur.fetchone()[0]

        bat_params = spec["battery"]
        cur.execute("""
            INSERT INTO batteries (customer_id, brand, capacity_kwh, max_charge_kw,
                                   max_discharge_kw, round_trip_eff, reserve_soc_pct,
                                   cycle_cap_per_day, current_soc_kwh, strategy_preset)
            VALUES (%s, 'SimBat', %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, [
            cust_id,
            bat_params["capacity_kwh"], bat_params["max_charge_kw"],
            bat_params["max_discharge_kw"], bat_params["round_trip_eff"],
            bat_params["reserve_soc_pct"], bat_params["cycle_cap_per_day"],
            bat_params["capacity_kwh"] * 0.5,   # start at 50% SOC
            json.dumps({"forecast_model": spec["forecast_model"],
                        **MODELS[spec["forecast_model"]]}),
        ])
        bat_id = cur.fetchone()[0]

        hh = spec["household"]
        cur.execute("""
            INSERT INTO households (customer_id, annual_kwh, has_solar, solar_kwp,
                                    has_ev, occupancy_profile, load_volatility)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, [
            cust_id, hh["annual_kwh"], hh["has_solar"], hh["solar_kwp"],
            hh["has_ev"], hh["occupancy_profile"], hh["load_volatility"],
        ])

        seeded.append({
            "handle": spec["handle"],
            "customer_id": cust_id,
            "battery_id": bat_id,
            "household": hh,
            "forecast_model": spec["forecast_model"],
            "battery_params": {**bat_params, "current_soc_kwh": bat_params["capacity_kwh"] * 0.5},
        })
        print(f"  Seeded {spec['handle']}: customer={cust_id} battery={bat_id}")
    return seeded


def _run_customer(
    cur: psycopg.Cursor,
    record: dict,
    slots: list[str],
    import_prices: list[float],
    export_prices: list[float],
) -> dict:
    """Run policy + oracle for one customer; bulk-write trades and benchmarks."""
    cust_id = record["customer_id"]
    bat_id = record["battery_id"]
    hh_params = record["household"]
    seed = stable_seed(record["handle"])

    series = generate_slots(hh_params, slots, seed=seed)
    actual_map = {s.slot_start: s for s in series["actual"]}
    # The forecast is the agent's lever: a model that has learned more of the home's
    # routine settles closer to the oracle. The naive forecast is the knowledge-0 floor.
    forecast_map = forecast_series(hh_params, slots, MODELS[record["forecast_model"]], seed=seed)

    bat_spec = record["battery_params"]

    # Policy: plan the dispatch on the forecast, settle each action on the actual.
    total_cashflow, trades = plan_and_settle(
        bat_spec, slots, import_prices, export_prices,
        forecast_map, actual_map,
    )
    trade_rows = [
        (bat_id, cust_id, t["sim_time"], t["action"], t["energy_kwh"],
         t["price_p_per_kwh"], t["cashflow"], t["cycles_used"])
        for t in trades
    ]

    # Oracle on the full span with SOC carry-over → one benchmark row per day.
    oracle_input = [
        (sl, imp, exp, actual_map[sl].load_kwh, actual_map[sl].solar_kwh)
        for sl, imp, exp in zip(slots, import_prices, export_prices)
    ]
    _, _, bench = run_window(bat_spec, oracle_input)
    benchmark_rows = [(cust_id, b["window_start"], b["window_end"], b["optimal_savings"]) for b in bench]

    # Bulk insert trades.
    cur.executemany("""
        INSERT INTO trades (battery_id, customer_id, sim_time, action, energy_kwh,
                            price_p_per_kwh, cashflow, cycles_used)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, trade_rows)

    # Bulk upsert benchmarks.
    cur.executemany("""
        INSERT INTO benchmarks (customer_id, window_start, window_end, optimal_savings, updated_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (customer_id, window_start) DO UPDATE
          SET optimal_savings = EXCLUDED.optimal_savings,
              updated_at = now()
    """, benchmark_rows)

    return {
        "handle": record["handle"],
        "customer_id": cust_id,
        "trade_count": len(trade_rows),
        "benchmark_count": len(benchmark_rows),
        "total_cashflow": round(total_cashflow, 4),
        "total_optimal": round(sum(b[3] for b in benchmark_rows), 4),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("DATABASE_URL not set.")

    print(f"Loading {args.days} days of prices from DuckDB...")
    conn_ddb = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    slots, import_prices, export_prices = _load_prices(conn_ddb, args.days)
    conn_ddb.close()
    print(f"  {len(slots)} slots ({slots[0][:10]} → {slots[-1][:10]})\n")

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            if args.reset:
                print("Resetting sim data...")
                _reset(cur)
                conn.commit()

            print("Seeding customers...")
            records = _seed_customers(cur)
            conn.commit()

            print("\nRunning sim...\n")
            results = []
            for record in records:
                print(f"  {record['handle']}...")
                r = _run_customer(cur, record, slots, import_prices, export_prices)
                results.append(r)
            conn.commit()

    # Print leaderboard.
    col = 18
    print(f"\n{'Handle':<{col}} {'Trades':>7} {'Bench':>7} {'Cashflow':>10} {'Optimal':>10} {'%':>8}")
    print("-" * (col + 46))
    for r in results:
        pct = 100 * r["total_cashflow"] / r["total_optimal"] if r["total_optimal"] > 0 else 0
        print(
            f"{r['handle']:<{col}} {r['trade_count']:>7} {r['benchmark_count']:>7}"
            f" {r['total_cashflow']:>10.4f} {r['total_optimal']:>10.4f} {pct:>7.1f}%"
        )


if __name__ == "__main__":
    main()
