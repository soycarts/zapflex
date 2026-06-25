"""Benchmark oracle: perfect-hindsight optimum against the ACTUAL household.

Formulated as a linear program over the real battery constraints, solved with
perfect knowledge of actual prices and actual household load/solar. The live
policy's own realised actions are always a feasible point of this LP with the
same objective, so the oracle's optimum is provably >= the policy's cashflow in
every scenario — which is what makes pct_of_optimal a sound [0, 1] skill metric.

Decision variables per half-hour slot:
  g  = energy drawn from the grid to charge (kWh)
  di = energy discharged that offsets import  (valued at import price, <= net load)
  de = energy discharged that is exported      (valued at export price)

Objective (maximise): sum( import_p*di + export_p*de - import_p*g ) / 100
which is exactly the settle() cashflow, written linearly.

Constraints mirror energy/battery.py:
  - charge stores g*rte; reserve is a discharge floor, not a hard SOC floor;
  - SOC stays within [0, capacity];
  - per-slot discharge <= max_discharge_kw * slot_hours;
  - per-day throughput (g*rte + di + de) <= cycle_cap_per_day * capacity.

Outputs one optimal_savings figure per customer per sim day, which the
mart_leaderboard divides into captured savings to compute pct_of_optimal.
"""
from __future__ import annotations
import datetime

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import csr_matrix


def _date_of(iso: str) -> str:
    return iso[:10]


def _solve(
    battery_params: dict,
    slots: list[tuple[str, float, float, float, float]],
    soc0: float,
    slot_hours: float = 0.5,
    export_floor_p: float = 0.0,
) -> tuple[float, list[dict]]:
    """Solve the dispatch LP across the given slots.

    slots: list of (sim_time, import_p, export_p, load_kwh, solar_kwh),
           in chronological order, possibly spanning multiple days. When called
           by the oracle these are ACTUAL values (perfect hindsight); when called
           by the policy they are the FORECAST.
    soc0:  state of charge (kWh) at the start of the first slot.
    export_floor_p: planning knob — never plan to export in slots whose export
           price is below this floor (the oracle leaves it at 0). Avoids dumping
           stored energy at uneconomic export prices.

    Returns (total_cashflow_gbp, list of trade dicts). The cycle cap is applied
    per calendar day; SOC carries across day boundaries within the window.
    """
    n = len(slots)
    if n == 0:
        return 0.0, []

    cap = battery_params["capacity_kwh"]
    rte = battery_params["round_trip_eff"]
    reserve = cap * battery_params["reserve_soc_pct"]
    mc = battery_params["max_charge_kw"] * slot_hours       # max grid draw / slot
    md = battery_params["max_discharge_kw"] * slot_hours    # max delivered / slot
    cycle_cap = battery_params["cycle_cap_per_day"]

    import_p = [s[1] for s in slots]
    export_p = [s[2] for s in slots]
    net = [max(0.0, s[3] - s[4]) for s in slots]  # import-offsetting capacity

    # Variable layout: [g_0..g_{n-1}, di_0.., de_0..] → 3n columns.
    G = lambda i: i
    DI = lambda i: n + i
    DE = lambda i: 2 * n + i
    ncol = 3 * n

    # Objective (linprog minimises): minimise -(p*di + e*de - p*g)/100.
    c = np.zeros(ncol)
    for i in range(n):
        c[G(i)] = import_p[i] / 100.0
        c[DI(i)] = -import_p[i] / 100.0
        c[DE(i)] = -export_p[i] / 100.0

    # Bounds. Export (de) is blocked in slots priced below the export floor.
    de_hi = [md if export_p[i] >= export_floor_p else 0.0 for i in range(n)]
    bounds = [(0.0, mc)] * n + [(0.0, net[i]) for i in range(n)] + [(0.0, de_hi[i]) for i in range(n)]

    rows, cols, data, b_ub = [], [], [], []
    r = 0

    def add(coef: dict, rhs: float) -> None:
        nonlocal r
        for col, v in coef.items():
            rows.append(r)
            cols.append(col)
            data.append(v)
        b_ub.append(rhs)
        r += 1

    # Per-slot discharge rate: di_i + de_i <= md.
    for i in range(n):
        add({DI(i): 1.0, DE(i): 1.0}, md)

    # Cumulative SOC ceiling and reserve/discharge floor.
    # ceiling: sum_{j<=i}(rte*g - di - de) <= cap - soc0
    # floor:   sum_{j<=i}(di + de - rte*g) <= max(0, soc0 - reserve)
    floor_rhs = max(0.0, soc0 - reserve)
    up_coef: dict[int, float] = {}
    lo_coef: dict[int, float] = {}
    for i in range(n):
        up_coef[G(i)] = rte
        up_coef[DI(i)] = -1.0
        up_coef[DE(i)] = -1.0
        lo_coef[G(i)] = -rte
        lo_coef[DI(i)] = 1.0
        lo_coef[DE(i)] = 1.0
        add(dict(up_coef), cap - soc0)
        add(dict(lo_coef), floor_rhs)

    # Per-day cycle cap: sum_day(rte*g + di + de) <= cycle_cap * cap.
    day_slots: dict[str, list[int]] = {}
    for i, s in enumerate(slots):
        day_slots.setdefault(_date_of(s[0]), []).append(i)
    for idxs in day_slots.values():
        coef = {}
        for i in idxs:
            coef[G(i)] = rte
            coef[DI(i)] = 1.0
            coef[DE(i)] = 1.0
        add(coef, cycle_cap * cap)

    A_ub = csr_matrix((data, (rows, cols)), shape=(r, ncol))
    res = linprog(c, A_ub=A_ub, b_ub=np.array(b_ub), bounds=bounds, method="highs")
    if not res.success:
        raise RuntimeError(f"oracle LP failed: {res.message}")

    x = res.x
    tol = 1e-7
    trades: list[dict] = []
    total = 0.0
    for i, (sim_time, imp, exp, _a_load, _a_solar) in enumerate(slots):
        g = max(0.0, x[G(i)])
        di = max(0.0, x[DI(i)])
        de = max(0.0, x[DE(i)])
        dis = di + de
        cashflow = (imp * di + exp * de - imp * g) / 100.0
        total += cashflow

        if dis > tol and dis >= g:
            action = "discharge"
            energy = dis
            price = (imp * di + exp * de) / dis
        elif g > tol:
            action = "charge"
            energy = g
            price = imp
        else:
            action = "idle"
            energy = 0.0
            price = imp

        cycles_used = (rte * g + dis) / cap
        trades.append({
            "sim_time": sim_time,
            "action": action,
            "energy_kwh": round(energy, 4),
            "price_p_per_kwh": round(price, 4),
            "cashflow": round(cashflow, 6),
            "cycles_used": round(cycles_used, 4),
        })

    return round(total, 6), trades


def run_day(
    battery_params: dict,
    day_slots: list[tuple[str, float, float, float, float]],
    slot_hours: float = 0.5,
) -> tuple[float, list[dict]]:
    """Compute optimal cashflow for one sim day with perfect hindsight.

    day_slots: list of (sim_time, import_p, export_p, actual_load_kwh, actual_solar_kwh).
    Returns (total_cashflow_gbp, list of trade dicts).
    """
    soc0 = battery_params.get("current_soc_kwh", 0.0)
    return _solve(battery_params, day_slots, soc0, slot_hours)


def run_window(
    battery_params: dict,
    slots: list[tuple[str, float, float, float, float]],
) -> tuple[float, list[dict], list[dict]]:
    """Run the oracle across multiple days as one global LP.

    The cycle cap is enforced per calendar day; SOC carries across days inside
    the optimisation, so the result is the true horizon optimum. Returns
    (total_cashflow, all_trades, benchmark_rows) with one benchmark row per day.
    """
    if not slots:
        return 0.0, [], []

    soc0 = battery_params.get("current_soc_kwh", 0.0)
    total, all_trades = _solve(battery_params, slots, soc0)

    # One benchmark row per day: optimal_savings = sum of that day's cashflow.
    by_day: dict[str, list[dict]] = {}
    for t in all_trades:
        by_day.setdefault(_date_of(t["sim_time"]), []).append(t)

    benchmark_rows: list[dict] = []
    for day in sorted(by_day):
        day_trades = by_day[day]
        first_slot = day_trades[0]["sim_time"]
        last_slot = day_trades[-1]["sim_time"]
        last_dt = datetime.datetime.fromisoformat(last_slot.replace("Z", "+00:00"))
        window_end = (last_dt + datetime.timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
        benchmark_rows.append({
            "window_start": first_slot,
            "window_end": window_end,
            "optimal_savings": round(sum(t["cashflow"] for t in day_trades), 6),
        })

    return round(total, 6), all_trades, benchmark_rows
