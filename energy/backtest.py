"""Offline DuckDB backtest: policy (with settlement) vs oracle across household types.

Run: python3 -m energy.backtest [--days N]
Reads prices from data/agile.duckdb.
"""
import argparse
from pathlib import Path

import duckdb

from energy.battery import Battery
from energy.household import generate_slots
from energy.policy import run_slot, DEFAULT_PRESET
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

# Three household types representing the leaderboard spread.
HOUSEHOLDS = {
    "stable_no_solar": {
        "annual_kwh": 3500, "has_solar": False, "solar_kwp": 0,
        "has_ev": False, "occupancy_profile": "standard", "load_volatility": 0.10,
    },
    "solar_no_ev": {
        "annual_kwh": 4000, "has_solar": True, "solar_kwp": 4.0,
        "has_ev": False, "occupancy_profile": "standard", "load_volatility": 0.15,
    },
    "solar_and_ev": {
        "annual_kwh": 5500, "has_solar": True, "solar_kwp": 4.0,
        "has_ev": True, "occupancy_profile": "standard", "load_volatility": 0.20,
    },
}

# Presets: default, well-matched for solar, mismatched (export-aggressive on no-solar home).
PRESETS = {
    "default": DEFAULT_PRESET,
    "export_tuned": {              # good for solar: low export threshold, higher reserve buffer
        **DEFAULT_PRESET,
        "export_threshold_p": 12.0,
        "reserve_soc_pct": 0.15,
        "min_spread_p": 5.0,
    },
    "mismatched": {               # stable-home preset applied to a solar+EV household
        **DEFAULT_PRESET,
        "export_threshold_p": 50.0,  # almost never exports (misses solar peaks)
        "discharge_dearest_slots": 4, # rarely discharges (assumes smooth load)
        "reserve_soc_pct": 0.05,     # thin buffer, exposed when EV spikes
        "min_spread_p": 18.0,        # requires large spread, misses most slots
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


def _run_policy(
    slots: list[str],
    import_prices: list[float],
    export_prices: list[float],
    household_params: dict,
    preset: dict,
    seed: int,
) -> float:
    series = generate_slots(household_params, slots, seed=seed)
    forecast_map = {s.slot_start: s for s in series["forecast"]}
    actual_map = {s.slot_start: s for s in series["actual"]}

    # Build per-day price lookup for slot ranking.
    by_day: dict[str, list[float]] = {}
    for slot, imp in zip(slots, import_prices):
        by_day.setdefault(slot[:10], []).append(imp)

    bat = Battery(**{k: BATTERY_PARAMS[k] for k in (
        "capacity_kwh", "max_charge_kw", "max_discharge_kw",
        "round_trip_eff", "reserve_soc_pct", "cycle_cap_per_day",
    )}, current_soc_kwh=BATTERY_PARAMS["current_soc_kwh"])

    total = 0.0
    prev_day = None
    for slot, imp, exp in zip(slots, import_prices, export_prices):
        day = slot[:10]
        if day != prev_day:
            bat.reset_daily_cycles()
            prev_day = day
        f = forecast_map[slot]
        a = actual_map[slot]
        trade = run_slot(
            battery=bat,
            sim_time=slot,
            import_p=imp,
            export_p=exp,
            day_import_prices=by_day[day],
            preset=preset,
            forecast_load_kwh=f.load_kwh,
            forecast_solar_kwh=f.solar_kwh,
            actual_load_kwh=a.load_kwh,
            actual_solar_kwh=a.solar_kwh,
        )
        total += trade["cashflow"]
    return round(total, 4)


def _run_oracle(
    slots: list[str],
    import_prices: list[float],
    export_prices: list[float],
    household_params: dict,
    seed: int,
) -> float:
    series = generate_slots(household_params, slots, seed=seed)
    actual_map = {s.slot_start: s for s in series["actual"]}
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

    col_w = 30
    print(f"{'Scenario':<{col_w}} {'Cashflow':>12} {'Oracle':>10} {'% optimal':>10}")
    print("-" * (col_w + 36))

    # Test matrix: show how household fit drives spread.
    # stable   → default only (predictable, strategy barely matters)
    # solar    → default vs export_tuned (matching export bet to solar pays off)
    # solar+EV → default vs export_tuned vs mismatched (EV volatility is the hard problem;
    #            thin reserve + aggressive discharge = exposed by EV spikes)
    scenarios = [
        ("stable_no_solar", "default"),
        ("solar_no_ev",     "default"),
        ("solar_no_ev",     "export_tuned"),
        ("solar_and_ev",    "default"),
        ("solar_and_ev",    "export_tuned"),
        ("solar_and_ev",    "mismatched"),
    ]

    prev_hh = None
    for hh_name, preset_name in scenarios:
        if hh_name != prev_hh:
            if prev_hh is not None:
                print()
            prev_hh = hh_name
        hh_params = HOUSEHOLDS[hh_name]
        preset = PRESETS[preset_name]
        seed = hash(hh_name) & 0xFFFF
        oracle_cf = _run_oracle(slots, import_prices, export_prices, hh_params, seed)
        cf = _run_policy(slots, import_prices, export_prices, hh_params, preset, seed)
        label = f"{hh_name}/{preset_name}"
        pct = f"{100 * cf / oracle_cf:.1f}%" if oracle_cf > 0 else "n/a"
        print(f"{label:<{col_w}} {cf:>12.4f} {oracle_cf:>10.4f} {pct:>10}")


if __name__ == "__main__":
    main()
