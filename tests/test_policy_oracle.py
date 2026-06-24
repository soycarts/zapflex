"""Invariant tests for policy and oracle using the new forecast/actual interface."""
import pytest
from energy.battery import Battery
from energy.policy import run_slot, DEFAULT_PRESET
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


def _run_policy_day(preset=DEFAULT_PRESET):
    bat = Battery(**{k: BATTERY_PARAMS[k] for k in (
        "capacity_kwh", "max_charge_kw", "max_discharge_kw",
        "round_trip_eff", "reserve_soc_pct", "cycle_cap_per_day",
    )}, current_soc_kwh=BATTERY_PARAMS["current_soc_kwh"])
    trades = []
    for i, slot in enumerate(_SLOTS):
        t = run_slot(bat, slot, _IMP[i], _EXP[i], _IMP, preset,
                     _LOAD[i], _SOLAR[i], _LOAD[i], _SOLAR[i])
        trades.append(t)
    return bat, trades


def test_policy_no_overcycle():
    bat, _ = _run_policy_day()
    assert bat.cycles_today <= bat.cycle_cap_per_day + 1e-9


def test_policy_soc_within_bounds():
    bat, _ = _run_policy_day()
    assert bat.current_soc_kwh >= bat.reserve_kwh - 1e-9
    assert bat.current_soc_kwh <= bat.capacity_kwh + 1e-9


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
    _, trades = _run_policy_day()
    policy_cf = sum(t["cashflow"] for t in trades)
    assert oracle_cf >= policy_cf - 1e-4


def test_pct_of_optimal_in_range():
    oracle_slots = [(s, i, e, l, so)
                    for s, i, e, l, so in zip(_SLOTS, _IMP, _EXP, _LOAD, _SOLAR)]
    oracle_cf, _, _ = run_window(BATTERY_PARAMS, oracle_slots)
    _, trades = _run_policy_day()
    policy_cf = sum(t["cashflow"] for t in trades)
    if oracle_cf > 0:
        pct = policy_cf / oracle_cf
        assert -0.05 <= pct <= 1.05
