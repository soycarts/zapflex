"""Tests for the battery model: SOC bounds, cycle cap, efficiency."""
import pytest
from energy.battery import Battery


def make_bat(**kwargs) -> Battery:
    defaults = dict(
        capacity_kwh=10.0, max_charge_kw=3.6, max_discharge_kw=3.6,
        round_trip_eff=0.90, reserve_soc_pct=0.10, cycle_cap_per_day=1.5,
        current_soc_kwh=5.0,
    )
    return Battery(**{**defaults, **kwargs})


def test_charge_increases_soc():
    bat = make_bat(current_soc_kwh=0.0)
    drawn = bat.charge()
    assert drawn > 0
    assert bat.current_soc_kwh > 0


def test_discharge_decreases_soc():
    bat = make_bat(current_soc_kwh=9.0)
    delivered = bat.discharge()
    assert delivered > 0
    assert bat.current_soc_kwh < 9.0


def test_soc_never_exceeds_capacity():
    bat = make_bat(current_soc_kwh=9.9)
    for _ in range(10):
        bat.charge()
    assert bat.current_soc_kwh <= bat.capacity_kwh + 1e-9


def test_soc_never_below_reserve():
    bat = make_bat(current_soc_kwh=1.1)  # just above reserve (10% of 10 = 1.0)
    for _ in range(20):
        bat.discharge()
    assert bat.current_soc_kwh >= bat.reserve_kwh - 1e-9


def test_cycle_cap_respected():
    bat = make_bat(current_soc_kwh=0.0)
    for _ in range(100):
        bat.charge()
        bat.discharge()
    assert bat.cycles_today <= bat.cycle_cap_per_day + 1e-9


def test_round_trip_efficiency():
    bat = make_bat(current_soc_kwh=0.0, round_trip_eff=0.90)
    grid_drawn = bat.charge()
    stored = bat.current_soc_kwh
    assert abs(stored - grid_drawn * 0.90) < 1e-6


def test_reset_daily_cycles():
    bat = make_bat(current_soc_kwh=5.0)
    bat.charge()
    bat.reset_daily_cycles()
    assert bat.cycles_today == 0.0


def test_no_charge_when_full():
    bat = make_bat(current_soc_kwh=10.0)
    drawn = bat.charge()
    assert drawn == 0.0


def test_no_discharge_at_reserve():
    bat = make_bat(current_soc_kwh=1.0)  # exactly at reserve (10% of 10)
    delivered = bat.discharge()
    assert delivered == 0.0
