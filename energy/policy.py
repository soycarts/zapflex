"""Strategy executor: plan a day-ahead dispatch on the FORECAST, settle on ACTUAL.

Real home-battery optimisers plan against a price + load forecast and settle on
what actually happened. We do the same: run the dispatch optimiser
(energy/optimizer.py) on the household forecast (energy/forecast.py) — capturing the
full multi-cycle value of the fully-visible Agile price curve — then settle the
planned actions against the realised household.

Because the price curve is fully visible, the only error left is household forecast
error. So pct_of_optimal is a clean measure of FORECAST SKILL: the better the agent
has learned a home's routine (its EV schedule, its solar climatology), the closer
the plan lands to the perfect-hindsight oracle. The unlearnable noise sets a ceiling
below 100% — perfect dispatch is impossible from the price curve alone, which is
exactly what makes the leaderboard a game.
"""
from __future__ import annotations

from energy.optimizer import _solve


def plan_and_settle(
    battery_params: dict,
    slots: list[str],
    import_prices: list[float],
    export_prices: list[float],
    forecast_map: dict,
    actual_map: dict,
    slot_hours: float = 0.5,
) -> tuple[float, list[dict]]:
    """Plan dispatch on the forecast, then settle each action on the actual household.

    forecast_map / actual_map: {slot_start: obj} with .load_kwh and .solar_kwh.
    Prices are known (the same for planning and settlement). The cycle cap is
    applied per calendar day and SOC carries across days inside the optimiser.

    Returns (total_settled_cashflow_gbp, list of per-slot trade dicts).
    """
    forecast_slots = [
        (s, imp, exp, forecast_map[s].load_kwh, forecast_map[s].solar_kwh)
        for s, imp, exp in zip(slots, import_prices, export_prices)
    ]
    soc0 = battery_params.get("current_soc_kwh", 0.0)
    _, plan = _solve(battery_params, forecast_slots, soc0, slot_hours=slot_hours)

    trades: list[dict] = []
    total = 0.0
    for t, imp, exp in zip(plan, import_prices, export_prices):
        a = actual_map[t["sim_time"]]
        cashflow, settled_price = settle(
            action=t["action"],
            energy_kwh=t["energy_kwh"],
            import_p=imp,
            export_p=exp,
            actual_net_load_kwh=a.load_kwh - a.solar_kwh,
        )
        total += cashflow
        trades.append({
            "sim_time": t["sim_time"],
            "action": t["action"],
            "energy_kwh": t["energy_kwh"],
            "price_p_per_kwh": round(settled_price, 4),
            "cashflow": round(cashflow, 6),
            "cycles_used": t["cycles_used"],
        })

    return round(total, 6), trades


def settle(
    action: str,
    energy_kwh: float,
    import_p: float,
    export_p: float,
    actual_net_load_kwh: float,
) -> tuple[float, float]:
    """Apply a planned battery action against actual load/solar.

    Returns (cashflow_gbp, effective_price_p_per_kwh).
    Discharge cashflow depends on whether actual household is a net consumer
    (saves import) or net exporter (adds export revenue).
    """
    if action == "idle" or energy_kwh == 0.0:
        return 0.0, import_p

    if action == "charge":
        return -(import_p / 100.0) * energy_kwh, import_p

    if action == "discharge":
        net = actual_net_load_kwh
        if net >= energy_kwh:
            # All discharge offsets grid import.
            return (import_p / 100.0) * energy_kwh, import_p
        elif net > 0.0:
            # Partial import offset, rest goes to export.
            import_cf = (import_p / 100.0) * net
            export_cf = (export_p / 100.0) * (energy_kwh - net)
            cashflow = import_cf + export_cf
            eff_price = (cashflow * 100.0) / energy_kwh
            return cashflow, eff_price
        else:
            # Net exporter: all discharge goes to grid.
            return (export_p / 100.0) * energy_kwh, export_p

    return 0.0, import_p
