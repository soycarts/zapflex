"""Benchmark oracle: perfect-hindsight optimum against the ACTUAL household.

Uses the same settlement function as the policy so the comparison is fair.
Outputs one optimal_savings figure per customer per sim day, which the
mart_leaderboard divides into captured savings to compute pct_of_optimal.
"""
from __future__ import annotations
import datetime
from energy.battery import Battery
from energy.policy import settle


def _date_of(iso: str) -> str:
    return iso[:10]


def run_day(
    battery_params: dict,
    day_slots: list[tuple[str, float, float, float, float]],
    slot_hours: float = 0.5,
) -> tuple[float, list[dict]]:
    """Compute optimal cashflow for one sim day with perfect hindsight.

    day_slots: list of (sim_time, import_p, export_p, actual_load_kwh, actual_solar_kwh).
    battery_params: capacity_kwh, max_charge_kw, max_discharge_kw, round_trip_eff,
                    reserve_soc_pct, cycle_cap_per_day, current_soc_kwh.

    Returns (total_cashflow_gbp, list of trade dicts).
    """
    n = len(day_slots)
    import_prices = [imp for _, imp, _, _, _ in day_slots]
    export_prices = [exp for _, _, exp, _, _ in day_slots]
    actual_loads = [l for _, _, _, l, _ in day_slots]
    actual_solars = [s for _, _, _, _, s in day_slots]
    actual_nets = [actual_loads[i] - actual_solars[i] for i in range(n)]

    # Effective discharge value per slot: if net consumer → saves import, else adds export.
    discharge_values = [
        import_prices[i] if actual_nets[i] > 0 else export_prices[i]
        for i in range(n)
    ]

    # Greedy assignment: cheapest import slots → charge; highest discharge-value slots → discharge.
    cycle_budget = battery_params["cycle_cap_per_day"]
    half_cycles = cycle_budget * 2  # each full-power half-hour = 0.5 cycles

    charge_order = sorted(range(n), key=lambda i: import_prices[i])
    discharge_order = sorted(range(n), key=lambda i: discharge_values[i], reverse=True)

    actions = ["idle"] * n
    charge_count = 0
    for idx in charge_order:
        if charge_count >= half_cycles:
            break
        actions[idx] = "charge"
        charge_count += 1

    discharge_count = 0
    for idx in discharge_order:
        if discharge_count >= half_cycles:
            break
        if actions[idx] != "charge":
            actions[idx] = "discharge"
            discharge_count += 1

    # Simulate the day, respecting SOC constraints.
    bat = Battery(
        capacity_kwh=battery_params["capacity_kwh"],
        max_charge_kw=battery_params["max_charge_kw"],
        max_discharge_kw=battery_params["max_discharge_kw"],
        round_trip_eff=battery_params["round_trip_eff"],
        reserve_soc_pct=battery_params["reserve_soc_pct"],
        cycle_cap_per_day=battery_params["cycle_cap_per_day"],
        current_soc_kwh=battery_params.get("current_soc_kwh", 0.0),
    )

    trades = []
    total_cashflow = 0.0

    for i, (sim_time, imp, exp, a_load, a_solar) in enumerate(day_slots):
        act = actions[i]
        energy_kwh = 0.0
        actual_action = "idle"

        if act == "charge":
            drawn = bat.charge(slot_hours)
            if drawn > 0:
                actual_action = "charge"
                energy_kwh = drawn
        elif act == "discharge":
            delivered = bat.discharge(slot_hours)
            if delivered > 0:
                actual_action = "discharge"
                energy_kwh = delivered

        cashflow, settled_price = settle(
            action=actual_action,
            energy_kwh=energy_kwh,
            import_p=imp,
            export_p=exp,
            actual_net_load_kwh=a_load - a_solar,
        )
        total_cashflow += cashflow
        trades.append({
            "sim_time": sim_time,
            "action": actual_action,
            "energy_kwh": round(energy_kwh, 4),
            "price_p_per_kwh": round(settled_price, 4),
            "cashflow": round(cashflow, 6),
            "cycles_used": round(energy_kwh / bat.capacity_kwh, 4) if actual_action != "idle" else 0.0,
        })

    return round(total_cashflow, 6), trades


def run_window(
    battery_params: dict,
    slots: list[tuple[str, float, float, float, float]],
) -> tuple[float, list[dict], list[dict]]:
    """Run the oracle across multiple days and return benchmark rows per day.

    Returns (total_cashflow, all_trades, benchmark_rows).
    benchmark_rows: list of dicts with customer_id placeholder (caller fills it),
                    window_start, window_end, optimal_savings.
    """
    # Group by date.
    by_day: dict[str, list] = {}
    for row in slots:
        day = _date_of(row[0])
        by_day.setdefault(day, []).append(row)

    all_trades: list[dict] = []
    benchmark_rows: list[dict] = []
    total = 0.0
    running_soc = battery_params.get("current_soc_kwh", 0.0)

    for day in sorted(by_day):
        day_slots = by_day[day]
        params_today = {**battery_params, "current_soc_kwh": running_soc}
        day_cf, day_trades = run_day(params_today, day_slots)

        # Carry SOC forward.
        if day_trades:
            last_soc = running_soc
            for t in day_trades:
                if t["action"] == "charge":
                    last_soc += t["energy_kwh"] * battery_params["round_trip_eff"]
                elif t["action"] == "discharge":
                    last_soc -= t["energy_kwh"]
            running_soc = max(
                battery_params["reserve_soc_pct"] * battery_params["capacity_kwh"],
                min(battery_params["capacity_kwh"], last_soc),
            )

        all_trades.extend(day_trades)
        total += day_cf

        # Build benchmark row for this day.
        first_slot = day_slots[0][0]
        last_slot = day_slots[-1][0]
        # window_end = 30 minutes after last slot_start
        last_dt = datetime.datetime.fromisoformat(last_slot.replace("Z", "+00:00"))
        window_end = (last_dt + datetime.timedelta(minutes=30)).isoformat().replace("+00:00", "Z")

        benchmark_rows.append({
            "window_start": first_slot,
            "window_end": window_end,
            "optimal_savings": round(day_cf, 6),
        })

    return round(total, 6), all_trades, benchmark_rows
