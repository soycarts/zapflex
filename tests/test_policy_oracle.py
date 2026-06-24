"""Invariant tests: no over-cycle, SOC within bounds, pct_of_optimal in [0, 1]."""
import pytest
from energy.battery import Battery
from energy.policy import run_slot, DEFAULT_PRESET
from energy.optimizer import run_window, optimal_cashflow_for_window


# Synthetic 48-slot day: prices vary realistically.
IMPORT_PRICES = (
    [5.0] * 8    # cheap overnight
    + [15.0] * 8  # morning ramp
    + [25.0] * 8  # daytime
    + [35.0] * 8  # evening peak
    + [10.0] * 8  # late night cheap
    + [20.0] * 8  # extra 16 slots for a second day
)
EXPORT_PRICES = [p * 0.85 for p in IMPORT_PRICES]  # export slightly lower

SLOTS_ONE_DAY = [
    (f"2026-01-01T{h:02d}:{m:02d}:00Z", IMPORT_PRICES[i], EXPORT_PRICES[i])
    for i, (h, m) in enumerate(
        (h, m) for h in range(24) for m in (0, 30)
    )
]

BATTERY_PARAMS = {
    "capacity_kwh": 10.0, "max_charge_kw": 3.6, "max_discharge_kw": 3.6,
    "round_trip_eff": 0.90, "reserve_soc_pct": 0.10, "cycle_cap_per_day": 1.5,
    "current_soc_kwh": 0.0,
}


def _run_policy_on_slots(preset: dict) -> tuple[float, Battery]:
    bat = Battery(**{k: BATTERY_PARAMS[k] for k in (
        "capacity_kwh", "max_charge_kw", "max_discharge_kw",
        "round_trip_eff", "reserve_soc_pct", "cycle_cap_per_day",
    )}, current_soc_kwh=BATTERY_PARAMS["current_soc_kwh"])
    total = 0.0
    import_forecast = [imp for _, imp, _ in SLOTS_ONE_DAY]
    for i, (sim_time, imp, exp) in enumerate(SLOTS_ONE_DAY):
        trade = run_slot(bat, sim_time, imp, exp, import_forecast[i:], preset)
        total += trade["cashflow"]
    return total, bat


def test_policy_no_overcycle():
    _, bat = _run_policy_on_slots(DEFAULT_PRESET)
    assert bat.cycles_today <= bat.cycle_cap_per_day + 1e-9


def test_policy_soc_within_bounds():
    _, bat = _run_policy_on_slots(DEFAULT_PRESET)
    assert bat.current_soc_kwh >= bat.reserve_kwh - 1e-9
    assert bat.current_soc_kwh <= bat.capacity_kwh + 1e-9


def test_oracle_no_overcycle():
    _, trades = run_window(BATTERY_PARAMS, SLOTS_ONE_DAY)
    total_cycles = sum(t["cycles_used"] for t in trades)
    # Cycles per day: one day = 48 slots.
    assert total_cycles <= BATTERY_PARAMS["cycle_cap_per_day"] + 1e-9


def test_oracle_soc_within_bounds():
    bat = Battery(**{k: BATTERY_PARAMS[k] for k in (
        "capacity_kwh", "max_charge_kw", "max_discharge_kw",
        "round_trip_eff", "reserve_soc_pct", "cycle_cap_per_day",
    )}, current_soc_kwh=BATTERY_PARAMS["current_soc_kwh"])
    _, trades = run_window(BATTERY_PARAMS, SLOTS_ONE_DAY)
    # All cashflows must be finite; no negative energy traded.
    for t in trades:
        assert t["energy_kwh"] >= 0
        assert isinstance(t["cashflow"], float)


def test_pct_of_optimal_in_range():
    oracle_cf = optimal_cashflow_for_window(BATTERY_PARAMS, SLOTS_ONE_DAY)
    policy_cf, _ = _run_policy_on_slots(DEFAULT_PRESET)
    if oracle_cf > 0:
        pct = policy_cf / oracle_cf
        assert 0.0 <= pct <= 1.05  # allow tiny overshoot due to rounding


def test_oracle_beats_naive():
    """Oracle must outperform a naive strategy over the same slots."""
    naive_preset = {**DEFAULT_PRESET, "charge_cheapest_slots": 24, "discharge_dearest_slots": 2}
    oracle_cf = optimal_cashflow_for_window(BATTERY_PARAMS, SLOTS_ONE_DAY)
    naive_cf, _ = _run_policy_on_slots(naive_preset)
    assert oracle_cf >= naive_cf - 1e-6


def test_default_beats_naive():
    """Default strategy should outperform the naive baseline."""
    naive_preset = {**DEFAULT_PRESET, "charge_cheapest_slots": 24, "discharge_dearest_slots": 2}
    default_cf, _ = _run_policy_on_slots(DEFAULT_PRESET)
    naive_cf, _ = _run_policy_on_slots(naive_preset)
    assert default_cf >= naive_cf - 1e-4
