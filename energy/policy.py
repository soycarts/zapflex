"""Strategy executor: runs a customer's strategy_preset per slot under hard caps.

Sees only the published forecast horizon (forecast_horizon_slots) — no perfect hindsight.
Hard caps (cycle_cap_per_day, reserve_soc_pct) are enforced regardless of strategy params.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from energy.battery import Battery

DEFAULT_PRESET = {
    "charge_cheapest_slots": 12,
    "discharge_dearest_slots": 10,
    "export_threshold_p": 25.0,
    "cost_per_cycle_p": 5.0,
    "reserve_soc_pct": 0.10,
    "forecast_horizon_slots": 48,
}


def run_slot(
    battery: "Battery",
    sim_time: str,
    import_p: float,
    export_p: float,
    forecast_import: list[float],  # upcoming import prices inc current slot
    preset: dict,
    net_load_kwh: float = 0.0,    # household load minus solar for this slot (kWh)
) -> dict:
    """Decide and execute action for one half-hour slot. Returns a trade record dict."""
    p = {**DEFAULT_PRESET, **preset}
    horizon = forecast_import[: p["forecast_horizon_slots"]]

    # Rank current price within the forecast horizon.
    sorted_asc = sorted(horizon)
    sorted_desc = sorted(horizon, reverse=True)

    n_cheap = p["charge_cheapest_slots"]
    n_dear = p["discharge_dearest_slots"]
    charge_threshold = sorted_asc[n_cheap - 1] if n_cheap <= len(sorted_asc) else float("inf")
    discharge_threshold = sorted_desc[n_dear - 1] if n_dear <= len(sorted_desc) else float("-inf")

    cost_per_cycle_p = p["cost_per_cycle_p"]

    action = "idle"
    energy_kwh = 0.0
    cashflow = 0.0

    if import_p <= charge_threshold and battery.cycles_today < battery.cycle_cap_per_day:
        # Cheap slot — charge.
        grid_drawn = battery.charge()
        if grid_drawn > 0:
            action = "charge"
            energy_kwh = grid_drawn
            cashflow = -(import_p / 100) * grid_drawn  # cost in GBP

    elif export_p >= p["export_threshold_p"] and battery.current_soc_kwh > battery.reserve_kwh:
        # High export price — export.
        delivered = battery.discharge()
        if delivered > 0:
            action = "discharge"
            energy_kwh = delivered
            cashflow = (export_p / 100) * delivered  # revenue in GBP

    elif import_p >= discharge_threshold and battery.current_soc_kwh > battery.reserve_kwh:
        # Dear import slot — discharge to avoid buying expensive grid power.
        delivered = battery.discharge()
        if delivered > 0:
            action = "discharge"
            energy_kwh = delivered
            # Saving = avoided grid cost for the energy we discharge.
            cashflow = (import_p / 100) * delivered

    cycles_used = energy_kwh / battery.capacity_kwh if action != "idle" else 0.0

    return {
        "sim_time": sim_time,
        "action": action,
        "energy_kwh": round(energy_kwh, 4),
        "price_p_per_kwh": import_p if action == "charge" else export_p if action == "discharge" else import_p,
        "cashflow": round(cashflow, 6),
        "cycles_used": round(cycles_used, 4),
    }
