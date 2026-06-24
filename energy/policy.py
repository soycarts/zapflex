"""Strategy executor: decides each slot from known prices + forecast load/solar.

Prices are fully visible (the 48-slot Agile day-ahead curve, as in production).
The policy sees a smooth forecast of household load and solar; settlement applies
the chosen action against the ACTUAL realised values to compute true cashflow.
Hard caps are enforced on the actual outcome.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from energy.battery import Battery

DEFAULT_PRESET = {
    "charge_cheapest_slots": 12,
    "discharge_dearest_slots": 10,
    "export_threshold_p": 25.0,
    "min_spread_p": 8.0,
    "cost_per_cycle_p": 5.0,
    "reserve_soc_pct": 0.10,
}


def run_slot(
    battery: "Battery",
    sim_time: str,
    import_p: float,
    export_p: float,
    day_import_prices: list[float],   # all 48 prices for the current sim day
    preset: dict,
    forecast_load_kwh: float = 0.0,   # smooth forecast for this slot
    forecast_solar_kwh: float = 0.0,
    actual_load_kwh: float = 0.0,     # realised value for settlement
    actual_solar_kwh: float = 0.0,
) -> dict:
    """Decide and execute action for one half-hour slot.

    Decision uses known prices and the forecast household.
    Cashflow is settled against actual load and solar.
    Returns a trade record dict ready for the trades table.
    """
    p = {**DEFAULT_PRESET, **preset}

    # Rank current slot within today's full 48-price curve.
    sorted_asc = sorted(day_import_prices)
    sorted_desc = sorted(day_import_prices, reverse=True)

    n_cheap = min(int(p["charge_cheapest_slots"]), len(sorted_asc))
    n_dear = min(int(p["discharge_dearest_slots"]), len(sorted_desc))
    charge_threshold = sorted_asc[n_cheap - 1] if n_cheap > 0 else float("inf")
    discharge_threshold = sorted_desc[n_dear - 1] if n_dear > 0 else float("-inf")

    # Combined spread threshold: min_spread_p + cost_per_cycle_p
    spread_min = float(p["min_spread_p"]) + float(p["cost_per_cycle_p"])

    # Forecast net load: positive = net consumer, negative = net exporter.
    forecast_net = forecast_load_kwh - forecast_solar_kwh

    # Effective discharge value given the forecast household state.
    # If we expect to be a net consumer, discharge saves import; otherwise adds export.
    if forecast_net > 0:
        forecast_discharge_price = import_p
    else:
        forecast_discharge_price = export_p

    action = "idle"
    energy_kwh = 0.0
    planned_price = import_p

    if import_p <= charge_threshold and battery.cycles_today < battery.cycle_cap_per_day:
        # Cheap slot: charge if the spread vs expected discharge is worth cycling.
        if (discharge_threshold - import_p) >= spread_min:
            grid_drawn = battery.charge()
            if grid_drawn > 0:
                action = "charge"
                energy_kwh = grid_drawn
                planned_price = import_p

    elif (
        export_p >= float(p["export_threshold_p"])
        and (export_p - charge_threshold) >= spread_min
        and battery.current_soc_kwh > battery.reserve_kwh
    ):
        # Export opportunity: price above threshold with enough spread.
        delivered = battery.discharge()
        if delivered > 0:
            action = "discharge"
            energy_kwh = delivered
            planned_price = export_p

    elif (
        import_p >= discharge_threshold
        and (import_p - charge_threshold) >= spread_min
        and battery.current_soc_kwh > battery.reserve_kwh
    ):
        # Expensive import slot: discharge to avoid grid draw.
        delivered = battery.discharge()
        if delivered > 0:
            action = "discharge"
            energy_kwh = delivered
            planned_price = import_p

    # Settlement: compute true cashflow from the actual household.
    cashflow, settled_price = settle(
        action=action,
        energy_kwh=energy_kwh,
        import_p=import_p,
        export_p=export_p,
        actual_net_load_kwh=actual_load_kwh - actual_solar_kwh,
    )

    cycles_used = energy_kwh / battery.capacity_kwh if action != "idle" else 0.0

    return {
        "sim_time": sim_time,
        "action": action,
        "energy_kwh": round(energy_kwh, 4),
        "price_p_per_kwh": round(settled_price, 4),
        "cashflow": round(cashflow, 6),
        "cycles_used": round(cycles_used, 4),
    }


def settle(
    action: str,
    energy_kwh: float,
    import_p: float,
    export_p: float,
    actual_net_load_kwh: float,
) -> tuple[float, float]:
    """Apply the planned battery action against actual load/solar.

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
