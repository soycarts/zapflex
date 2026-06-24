"""Offline DuckDB backtest: default strategy vs oracle vs naive baseline.

Run: python3 -m energy.backtest [--days N]
Reads from data/agile.duckdb. Prints captured savings and pct_of_optimal.
"""
import argparse
import copy
from pathlib import Path

import duckdb

from energy.battery import Battery
from energy.sim_clock import SimClock
from energy.policy import run_slot, DEFAULT_PRESET
from energy.optimizer import run_window

DUCKDB_PATH = Path(__file__).parent.parent / "data" / "agile.duckdb"

# A tuned-worse strategy: charges too many slots, discharges too few.
NAIVE_PRESET = {
    **DEFAULT_PRESET,
    "charge_cheapest_slots": 24,   # charges half the day indiscriminately
    "discharge_dearest_slots": 2,  # barely discharges
    "export_threshold_p": 50.0,    # almost never exports
}

BATTERY_PARAMS = {
    "capacity_kwh": 10.0,
    "max_charge_kw": 3.6,
    "max_discharge_kw": 3.6,
    "round_trip_eff": 0.90,
    "reserve_soc_pct": 0.10,
    "cycle_cap_per_day": 1.5,
    "current_soc_kwh": 5.0,
}


def _load_prices(days: int) -> tuple[list[dict], list[dict]]:
    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    # Take the most recent N days of data that have both directions.
    both_days = con.execute("""
        SELECT DISTINCT slot_start::date AS day
        FROM tariff_prices
        WHERE direction = 'import'
          AND slot_start::date IN (
              SELECT DISTINCT slot_start::date FROM tariff_prices WHERE direction = 'export'
          )
        ORDER BY day DESC
        LIMIT ?
    """, [days]).fetchall()
    if not both_days:
        raise RuntimeError("No price data in DuckDB. Run energy/ingest_agile.py first.")
    day_list = sorted(d[0].strftime("%Y-%m-%d") for d in both_days)
    imports = con.execute("""
        SELECT slot_start::text AS slot_start, price_p_per_kwh
        FROM tariff_prices WHERE direction='import'
          AND slot_start::date >= ? AND slot_start::date <= ?
        ORDER BY slot_start
    """, [day_list[0], day_list[-1]]).fetchall()
    exports = con.execute("""
        SELECT slot_start::text AS slot_start, price_p_per_kwh
        FROM tariff_prices WHERE direction='export'
          AND slot_start::date >= ? AND slot_start::date <= ?
        ORDER BY slot_start
    """, [day_list[0], day_list[-1]]).fetchall()
    con.close()
    return (
        [{"slot_start": r[0], "price_p_per_kwh": r[1]} for r in imports],
        [{"slot_start": r[0], "price_p_per_kwh": r[1]} for r in exports],
    )


def _run_policy(clock: SimClock, preset: dict, bat_params: dict) -> float:
    bat = Battery(**{k: bat_params[k] for k in (
        "capacity_kwh", "max_charge_kw", "max_discharge_kw",
        "round_trip_eff", "reserve_soc_pct", "cycle_cap_per_day",
    )}, current_soc_kwh=bat_params["current_soc_kwh"])

    total = 0.0
    slots = list(clock.iter_fast())
    forecast_import = [imp for _, imp, _ in slots]

    prev_date = None
    for i, (sim_time, imp, exp) in enumerate(slots):
        date = sim_time[:10]
        if date != prev_date:
            bat.reset_daily_cycles()
            prev_date = date
        trade = run_slot(
            battery=bat,
            sim_time=sim_time,
            import_p=imp,
            export_p=exp,
            forecast_import=forecast_import[i:],
            preset=preset,
        )
        total += trade["cashflow"]
    return round(total, 4)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    print(f"Loading {args.days} days of prices from DuckDB...")
    import_prices, export_prices = _load_prices(args.days)
    print(f"  {len(import_prices)} import slots, {len(export_prices)} export slots")

    clock = SimClock(import_prices, export_prices, seconds_per_slot=0)
    slots = list(clock.iter_fast())

    print("\nRunning oracle (perfect hindsight)...")
    oracle_cashflow, _ = run_window(BATTERY_PARAMS, slots)

    print("Running default strategy...")
    default_cashflow = _run_policy(clock, DEFAULT_PRESET, BATTERY_PARAMS)

    print("Running naive baseline...")
    naive_cashflow = _run_policy(clock, NAIVE_PRESET, BATTERY_PARAMS)

    print(f"\n{'Strategy':<20} {'Cashflow (GBP)':>15} {'% of optimal':>14}")
    print("-" * 52)

    def pct(val: float) -> str:
        if oracle_cashflow == 0:
            return "n/a"
        return f"{100 * val / oracle_cashflow:.1f}%"

    print(f"{'Oracle (optimal)':<20} {oracle_cashflow:>15.4f} {'100.0%':>14}")
    print(f"{'Default strategy':<20} {default_cashflow:>15.4f} {pct(default_cashflow):>14}")
    print(f"{'Naive baseline':<20} {naive_cashflow:>15.4f} {pct(naive_cashflow):>14}")


if __name__ == "__main__":
    main()
