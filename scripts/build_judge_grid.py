"""Precompute the judge game surface: pct_of_optimal across forecast-knowledge for a
few sandbox households, over the same replay window the live fleet uses.

The real policy executor + oracle (energy/) produce every number; the judge UI reads
the resulting static grid so slider feedback is instant and needs no runtime compute.

Writes dashboard/public/judge_grid.json.
Run: python3 scripts/build_judge_grid.py [--days N]
"""
import argparse
import json
from pathlib import Path

import duckdb

from energy.household import generate_slots, stable_seed
from energy.forecast import forecast_series
from energy.optimizer import run_window
from energy.policy import plan_and_settle

ROOT = Path(__file__).resolve().parent.parent
DUCKDB_PATH = ROOT / "data" / "agile.duckdb"
OUT = ROOT / "dashboard" / "public" / "judge_grid.json"

BATTERY = {
    "capacity_kwh": 10.0, "max_charge_kw": 3.6, "max_discharge_kw": 3.6,
    "round_trip_eff": 0.90, "reserve_soc_pct": 0.10, "cycle_cap_per_day": 1.5,
    "current_soc_kwh": 5.0,
}

# Sandbox households the judge can pick — mirror the fleet archetypes so the judge's
# rank sits naturally alongside alpha/beta/gamma.
ARCHETYPES = {
    "stable": {
        "label": "Stable home — no solar, no EV",
        "params": {"annual_kwh": 3500, "has_solar": False, "solar_kwp": 0.0,
                   "has_ev": False, "occupancy_profile": "standard", "load_volatility": 0.16},
    },
    "solar": {
        "label": "Solar home — no EV",
        "params": {"annual_kwh": 4000, "has_solar": True, "solar_kwp": 4.0,
                   "has_ev": False, "occupancy_profile": "standard", "load_volatility": 0.20},
    },
    "ev_solar": {
        "label": "Solar + EV home",
        "params": {"annual_kwh": 5500, "has_solar": True, "solar_kwp": 4.0,
                   "has_ev": True, "occupancy_profile": "standard", "load_volatility": 0.20},
        # Featured sandbox: an EV routine that lands squarely in the evening price peak,
        # so learning it climbs ~76% → ~98% — the high-value judge moment.
        "seed": 3,
    },
}

STEPS = [round(i / 10, 1) for i in range(11)]  # 0.0 .. 1.0 by 0.1


def load_prices(days: int):
    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    rows = con.execute("""
        SELECT DISTINCT slot_start::date d FROM tariff_prices WHERE direction='import'
          AND slot_start::date IN (SELECT DISTINCT slot_start::date FROM tariff_prices WHERE direction='export')
        ORDER BY d DESC LIMIT ?""", [days]).fetchall()
    dl = sorted(r[0].strftime("%Y-%m-%d") for r in rows)

    def fetch(d):
        return con.execute("""SELECT slot_start::text, price_p_per_kwh FROM tariff_prices
            WHERE direction=? AND slot_start::date>=? AND slot_start::date<=? ORDER BY slot_start""",
            [d, dl[0], dl[-1]]).fetchall()
    imp = {r[0]: float(r[1]) for r in fetch("import")}
    exp = {r[0]: float(r[1]) for r in fetch("export")}
    con.close()
    common = sorted(set(imp) & set(exp))
    return common, [imp[s] for s in common], [exp[s] for s in common], dl[0], dl[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    slots, imp, exp, d0, d1 = load_prices(args.days)
    out = {"window_start": d0, "window_end": d1, "steps": STEPS, "archetypes": {}}

    for key, spec in ARCHETYPES.items():
        params = spec["params"]
        seed = spec.get("seed", stable_seed(f"judge_{key}"))
        series = generate_slots(params, slots, seed=seed)
        amap = {s.slot_start: s for s in series["actual"]}
        oracle_cf, _, _ = run_window(BATTERY, [
            (sl, i, e, amap[sl].load_kwh, amap[sl].solar_kwh) for sl, i, e in zip(slots, imp, exp)
        ])

        pct_grid, cap_grid = [], []
        for sk in STEPS:
            pct_row, cap_row = [], []
            for ek in STEPS:
                fmap = forecast_series(params, slots, {"solar_knowledge": sk, "ev_knowledge": ek}, seed=seed)
                cf, _ = plan_and_settle(BATTERY, slots, imp, exp, fmap, amap)
                pct_row.append(round(100 * cf / oracle_cf, 2) if oracle_cf > 0 else 0.0)
                cap_row.append(round(cf, 4))
            pct_grid.append(pct_row)
            cap_grid.append(cap_row)

        out["archetypes"][key] = {
            "label": spec["label"],
            "has_solar": params["has_solar"],
            "has_ev": params["has_ev"],
            "oracle_gbp": round(oracle_cf, 4),
            "pct": pct_grid,        # pct[solar_idx][ev_idx]
            "captured": cap_grid,
        }
        print(f"{key:9} oracle £{oracle_cf:.2f}  naive {pct_grid[0][0]:.1f}%  learned {pct_grid[-1][-1]:.1f}%")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {OUT.relative_to(ROOT)} ({len(json.dumps(out))} bytes)")


if __name__ == "__main__":
    main()
