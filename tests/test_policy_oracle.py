"""Invariant tests for the forecast-planning policy and the oracle."""
import pytest
from energy.household import SlotSeries
from energy.policy import plan_and_settle
from energy.optimizer import run_window, run_day

BATTERY_PARAMS = {
    "capacity_kwh": 10.0, "max_charge_kw": 3.6, "max_discharge_kw": 3.6,
    "round_trip_eff": 0.90, "reserve_soc_pct": 0.10, "cycle_cap_per_day": 1.5,
    "current_soc_kwh": 0.0,
}

_SLOTS = [f"2026-01-01T{h:02d}:{m:02d}:00Z" for h in range(24) for m in (0, 30)]
_IMP = [5.0] * 8 + [15.0] * 8 + [25.0] * 8 + [35.0] * 8 + [10.0] * 8 + [20.0] * 8
_EXP = [p * 0.85 for p in _IMP]
_LOAD = [0.2] * 48   # flat 0.2 kWh per slot
_SOLAR = [0.0] * 48


def _run_policy_day():
    """Plan + settle on a flat household (forecast == actual)."""
    fmap = {s: SlotSeries(s, _LOAD[i], _SOLAR[i]) for i, s in enumerate(_SLOTS)}
    total, trades = plan_and_settle(BATTERY_PARAMS, _SLOTS, _IMP, _EXP, fmap, fmap)
    return total, trades


def _soc_trajectory(trades):
    """Reconstruct SOC after each slot from the executed trades."""
    soc = BATTERY_PARAMS["current_soc_kwh"]
    rte = BATTERY_PARAMS["round_trip_eff"]
    out = []
    for t in trades:
        if t["action"] == "charge":
            soc += t["energy_kwh"] * rte
        elif t["action"] == "discharge":
            soc -= t["energy_kwh"]
        out.append(soc)
    return out


def test_policy_no_overcycle():
    _, trades = _run_policy_day()
    total_cycles = sum(t["cycles_used"] for t in trades)
    assert total_cycles <= BATTERY_PARAMS["cycle_cap_per_day"] + 1e-9


def test_policy_soc_within_bounds():
    _, trades = _run_policy_day()
    # Tolerance covers rounding of per-slot energy to 4 dp accumulated over the day.
    for soc in _soc_trajectory(trades):
        assert soc >= -1e-3
        assert soc <= BATTERY_PARAMS["capacity_kwh"] + 1e-3


def test_oracle_no_overcycle():
    oracle_slots = [(s, i, e, l, so)
                    for s, i, e, l, so in zip(_SLOTS, _IMP, _EXP, _LOAD, _SOLAR)]
    _, trades, _ = run_window(BATTERY_PARAMS, oracle_slots)
    total_cycles = sum(t["cycles_used"] for t in trades)
    assert total_cycles <= BATTERY_PARAMS["cycle_cap_per_day"] + 1e-9


def test_oracle_beats_policy():
    oracle_slots = [(s, i, e, l, so)
                    for s, i, e, l, so in zip(_SLOTS, _IMP, _EXP, _LOAD, _SOLAR)]
    oracle_cf, _, _ = run_window(BATTERY_PARAMS, oracle_slots)
    policy_cf, _ = _run_policy_day()
    assert oracle_cf >= policy_cf - 1e-4


def test_pct_of_optimal_in_range():
    oracle_slots = [(s, i, e, l, so)
                    for s, i, e, l, so in zip(_SLOTS, _IMP, _EXP, _LOAD, _SOLAR)]
    oracle_cf, _, _ = run_window(BATTERY_PARAMS, oracle_slots)
    policy_cf, _ = _run_policy_day()
    if oracle_cf > 0:
        pct = policy_cf / oracle_cf
        assert -0.05 <= pct <= 1.05


def test_policy_matches_oracle_on_perfect_forecast():
    """With forecast == actual, the plan should capture ~all of the optimum."""
    oracle_slots = [(s, i, e, l, so)
                    for s, i, e, l, so in zip(_SLOTS, _IMP, _EXP, _LOAD, _SOLAR)]
    oracle_cf, _, _ = run_window(BATTERY_PARAMS, oracle_slots)
    policy_cf, _ = _run_policy_day()
    if oracle_cf > 0:
        assert policy_cf / oracle_cf >= 0.99
