"""Offline DuckDB backtest: forecast skill vs the perfect-hindsight oracle.

The Agile price curve is fully visible, so the only lever is how well the agent has
learned each household's routine. This backtest plays every household under every
named forecast model and prints pct_of_optimal — showing the best model shift from
'naive' on a predictable home to 'learned' on a home with an EV routine to capture.

Run: python3 -m energy.backtest [--days N]
Reads prices from data/agile.duckdb.
"""
import argparse
from pathlib import Path

import duckdb

from energy.household import generate_slots, stable_seed
from energy.forecast import forecast_series, MODELS
from energy.policy import plan_and_settle
from energy.optimizer import run_window

DUCKDB_PATH = Path(__file__).parent.parent / "data" / "agile.duckdb"

BATTERY_PARAMS = {
    "capacity_kwh": 10.0,
    "max_charge_kw": 3.6,
    "max_discharge_kw": 3.6,
    "round_trip_eff": 0.90,
    "reserve_soc_pct": 0.10,
    "cycle_cap_per_day": 1.5,
    "current_soc_kwh": 5.0,
}

# Household types spanning the leaderboard: how much learnable routine each has.
HOUSEHOLDS = {
    "stable_no_solar": {
        "annual_kwh": 3500, "has_solar": False, "solar_kwp": 0,
        "has_ev": False, "occupancy_profile": "standard", "load_volatility": 0.16,
    },
    "solar_no_ev": {
        "annual_kwh": 4000, "has_solar": True, "solar_kwp": 4.0,
        "has_ev": False, "occupancy_profile": "standard", "load_volatility": 0.20,
    },
    "solar_and_ev": {
        "annual_kwh": 5500, "has_solar": True, "solar_kwp": 4.0,
        "has_ev": True, "occupancy_profile": "standard", "load_volatility": 0.20,
    },
}


def _load_prices(days: int) -> tuple[list[str], list[float], list[float]]:
    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    day_rows = con.execute("""
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
        return con.execute("""
            SELECT slot_start::text, price_p_per_kwh
            FROM tariff_prices WHERE direction=?
              AND slot_start::date >= ? AND slot_start::date <= ?
            ORDER BY slot_start
        """, [direction, day_list[0], day_list[-1]]).fetchall()

    imp = fetch("import")
    exp = fetch("export")
    con.close()

    imp_map = {r[0]: float(r[1]) for r in imp}
    exp_map = {r[0]: float(r[1]) for r in exp}
    common = sorted(set(imp_map) & set(exp_map))
    return common, [imp_map[s] for s in common], [exp_map[s] for s in common]


def _oracle_cashflow(slots, import_prices, export_prices, actual_map) -> float:
    oracle_slots = [
        (slot, imp, exp, actual_map[slot].load_kwh, actual_map[slot].solar_kwh)
        for slot, imp, exp in zip(slots, import_prices, export_prices)
    ]
    total, _, _ = run_window(BATTERY_PARAMS, oracle_slots)
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    print(f"Loading {args.days} days of prices from DuckDB...")
    slots, import_prices, export_prices = _load_prices(args.days)
    print(f"  {len(slots)} slots\n")

    # Grid: every household under every forecast model. The winning model (marked *)
    # shifts from 'naive' on predictable homes to 'learned' on the EV home — capturing
    # the routine is worth real points, which is the forecast-skill game.
    model_names = list(MODELS)
    name_w, cell_w = 18, 12
    header = f"{'household':<{name_w}} {'oracle £':>9}  " + "".join(f"{m:>{cell_w}}" for m in model_names)
    print(header)
    print("-" * len(header))

    for hh_name, hh_params in HOUSEHOLDS.items():
        seed = stable_seed(hh_name)
        series = generate_slots(hh_params, slots, seed=seed)
        actual_map = {s.slot_start: s for s in series["actual"]}
        oracle_cf = _oracle_cashflow(slots, import_prices, export_prices, actual_map)

        pcts = {}
        for m_name in model_names:
            fmap = forecast_series(hh_params, slots, MODELS[m_name], seed=seed)
            cf, _ = plan_and_settle(BATTERY_PARAMS, slots, import_prices, export_prices, fmap, actual_map)
            pcts[m_name] = 100 * cf / oracle_cf if oracle_cf > 0 else 0.0
        best = max(pcts, key=pcts.get)
        cells = "".join(
            f"{(f'{pcts[m]:.1f}%' + ('*' if m == best else '')):>{cell_w}}"
            for m in model_names
        )
        print(f"{hh_name:<{name_w}} {oracle_cf:>9.2f}  {cells}")

    print("\n  * = best forecast model for that household (the routine the agent should learn)")


if __name__ == "__main__":
    main()
