"""Benchmark oracle: perfect-hindsight optimum over a window under the same hard caps.

Sees all prices in the window upfront. Maximises cashflow subject to:
- cycle_cap_per_day per sim day
- reserve_soc_pct floor
- capacity ceiling
- charge/discharge power limits

Uses a greedy dual-pass: charge in cheapest slots, discharge/export in dearest.
This achieves optimal or near-optimal for simple time-of-use arbitrage.
"""
from __future__ import annotations
import copy
from energy.battery import Battery


def run_window(
    battery_params: dict,
    slots: list[tuple[str, float, float]],  # (sim_time, import_p, export_p)
    slot_hours: float = 0.5,
) -> tuple[float, list[dict]]:
    """Compute optimal cashflow over the slot window with perfect hindsight.

    Returns (total_cashflow_gbp, list of trade dicts).
    battery_params keys: capacity_kwh, max_charge_kw, max_discharge_kw,
                         round_trip_eff, reserve_soc_pct, cycle_cap_per_day, current_soc_kwh.
    """
    # Group slots into sim days (48 slots each).
    days: list[list[tuple]] = []
    current_day: list[tuple] = []
    prev_date = None
    for sim_time, imp, exp in slots:
        date = sim_time[:10]
        if date != prev_date:
            if current_day:
                days.append(current_day)
            current_day = []
            prev_date = date
        current_day.append((sim_time, imp, exp))
    if current_day:
        days.append(current_day)

    bat = Battery(**{k: battery_params[k] for k in (
        "capacity_kwh", "max_charge_kw", "max_discharge_kw",
        "round_trip_eff", "reserve_soc_pct", "cycle_cap_per_day",
    )}, current_soc_kwh=battery_params.get("current_soc_kwh", 0.0))

    all_trades: list[dict] = []
    total_cashflow = 0.0

    for day_slots in days:
        bat.reset_daily_cycles()
        import_prices = [imp for _, imp, _ in day_slots]
        export_prices = [exp for _, _, exp in day_slots]

        # Rank each slot: cheaper = better to charge, dearer = better to discharge/export.
        n = len(day_slots)
        sorted_imp_asc = sorted(range(n), key=lambda i: import_prices[i])
        sorted_exp_desc = sorted(range(n), key=lambda i: export_prices[i], reverse=True)

        # Assign actions greedily: cheapest slots charge, dearest export/discharge.
        # Alternate passes until no improvement or cycle cap hit.
        actions = ["idle"] * n
        half_cycles = bat.cycle_cap_per_day * 2  # each charge or discharge = 0.5 cycles

        charge_count = 0
        discharge_count = 0
        for idx in sorted_imp_asc:
            if charge_count >= half_cycles:
                break
            actions[idx] = "charge"
            charge_count += 1

        for idx in sorted_exp_desc:
            if discharge_count >= half_cycles:
                break
            if actions[idx] != "charge":
                actions[idx] = "discharge"
                discharge_count += 1

        # Simulate the day with assigned actions, respecting SOC constraints.
        day_bat = Battery(
            capacity_kwh=bat.capacity_kwh,
            max_charge_kw=bat.max_charge_kw,
            max_discharge_kw=bat.max_discharge_kw,
            round_trip_eff=bat.round_trip_eff,
            reserve_soc_pct=bat.reserve_soc_pct,
            cycle_cap_per_day=bat.cycle_cap_per_day,
            current_soc_kwh=bat.current_soc_kwh,
        )

        for i, (sim_time, imp, exp) in enumerate(day_slots):
            act = actions[i]
            energy_kwh = 0.0
            cashflow = 0.0
            actual_action = "idle"

            if act == "charge":
                drawn = day_bat.charge(slot_hours)
                if drawn > 0:
                    actual_action = "charge"
                    energy_kwh = drawn
                    cashflow = -(imp / 100) * drawn
            elif act == "discharge":
                delivered = day_bat.discharge(slot_hours)
                if delivered > 0:
                    actual_action = "discharge"
                    energy_kwh = delivered
                    # Use best of export price and import savings.
                    price = max(exp, imp)
                    cashflow = (price / 100) * delivered

            cycles_used = energy_kwh / day_bat.capacity_kwh if actual_action != "idle" else 0.0
            total_cashflow += cashflow
            all_trades.append({
                "sim_time": sim_time,
                "action": actual_action,
                "energy_kwh": round(energy_kwh, 4),
                "price_p_per_kwh": imp if actual_action == "charge" else max(exp, imp),
                "cashflow": round(cashflow, 6),
                "cycles_used": round(cycles_used, 4),
            })

        bat.current_soc_kwh = day_bat.current_soc_kwh

    return round(total_cashflow, 6), all_trades


def optimal_cashflow_for_window(
    battery_params: dict,
    slots: list[tuple[str, float, float]],
) -> float:
    """Return just the total optimal cashflow (GBP) for use in leaderboard scoring."""
    cashflow, _ = run_window(battery_params, slots)
    return cashflow
