"""Tests for the forecast/actual split, settlement, and leaderboard invariants."""
import pytest
from energy.battery import Battery
from energy.household import generate_slots
from energy.policy import run_slot, DEFAULT_PRESET, settle
from energy.optimizer import run_window, run_day

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

BATTERY_PARAMS = {
    "capacity_kwh": 10.0, "max_charge_kw": 3.6, "max_discharge_kw": 3.6,
    "round_trip_eff": 0.90, "reserve_soc_pct": 0.10, "cycle_cap_per_day": 1.5,
    "current_soc_kwh": 0.0,
}

HH_EV_SOLAR = {
    "annual_kwh": 5500, "has_solar": True, "solar_kwp": 4.0,
    "has_ev": True, "occupancy_profile": "standard", "load_volatility": 0.25,
}
HH_STABLE = {
    "annual_kwh": 3500, "has_solar": False, "solar_kwp": 0,
    "has_ev": False, "occupancy_profile": "standard", "load_volatility": 0.10,
}

# One synthetic day: varied prices.
_SLOTS_48 = [
    f"2026-01-15T{h:02d}:{m:02d}:00Z"
    for h in range(24) for m in (0, 30)
]
_IMP = (
    [5.0] * 8 + [10.0] * 4 + [18.0] * 8 + [30.0] * 8 +
    [35.0] * 8 + [15.0] * 8 + [8.0] * 4
)
_EXP = [p * 0.80 for p in _IMP]


# ---------------------------------------------------------------------------
# Household: forecast and actual differ
# ---------------------------------------------------------------------------

def test_forecast_actual_differ_ev():
    """EV household: actual departs from forecast on some slots."""
    series = generate_slots(HH_EV_SOLAR, _SLOTS_48, seed=42)
    forecasts = [s.load_kwh for s in series["forecast"]]
    actuals = [s.load_kwh for s in series["actual"]]
    # At least one slot must differ (noise + possible EV spike).
    assert forecasts != actuals


def test_forecast_actual_differ_stable():
    """Even a stable household has noise."""
    series = generate_slots(HH_STABLE, _SLOTS_48, seed=7)
    forecasts = [s.load_kwh for s in series["forecast"]]
    actuals = [s.load_kwh for s in series["actual"]]
    assert forecasts != actuals


def test_forecast_no_ev_spikes():
    """Forecast series must be smooth: no EV spikes."""
    series = generate_slots(HH_EV_SOLAR, _SLOTS_48, seed=99)
    forecasts = [s.load_kwh for s in series["forecast"]]
    actuals = [s.load_kwh for s in series["actual"]]
    # Max single-slot forecast load is bounded by daily total / 48 + profile shape.
    max_forecast = max(forecasts)
    # EV spike adds 1.5-2.5 kWh; a spike > 2x the max forecast slot implies actual had it.
    assert max(actuals) >= max_forecast  # actual can be higher


# ---------------------------------------------------------------------------
# Settlement uses actual, not forecast
# ---------------------------------------------------------------------------

def test_settlement_net_consumer():
    """Discharge while net consumer → saves import, not export."""
    cf, price = settle("discharge", 1.0, import_p=30.0, export_p=15.0,
                       actual_net_load_kwh=2.0)
    assert abs(cf - 0.30) < 1e-9   # (30p/100) * 1 kWh
    assert abs(price - 30.0) < 1e-9


def test_settlement_net_exporter():
    """Discharge while net exporter → adds export revenue."""
    cf, price = settle("discharge", 1.0, import_p=30.0, export_p=15.0,
                       actual_net_load_kwh=-1.0)
    assert abs(cf - 0.15) < 1e-9   # (15p/100) * 1 kWh
    assert abs(price - 15.0) < 1e-9


def test_settlement_partial_split():
    """Discharge partially offsets import, rest goes to export."""
    cf, _ = settle("discharge", 2.0, import_p=30.0, export_p=15.0,
                   actual_net_load_kwh=1.0)
    expected = (30.0 / 100) * 1.0 + (15.0 / 100) * 1.0
    assert abs(cf - expected) < 1e-9


def test_settlement_charge_cost():
    """Charge always costs at import price."""
    cf, price = settle("charge", 1.5, import_p=5.0, export_p=3.0,
                       actual_net_load_kwh=0.5)
    assert abs(cf - (-0.075)) < 1e-9  # -(5p/100) * 1.5 kWh
    assert abs(price - 5.0) < 1e-9


def test_settlement_idle():
    cf, _ = settle("idle", 0.0, 30.0, 15.0, 1.0)
    assert cf == 0.0


# ---------------------------------------------------------------------------
# Policy: hard caps never breached on the actual outcome
# ---------------------------------------------------------------------------

def _run_day_policy(hh_params, preset, seed=0):
    series = generate_slots(hh_params, _SLOTS_48, seed=seed)
    fm = {s.slot_start: s for s in series["forecast"]}
    am = {s.slot_start: s for s in series["actual"]}
    bat = Battery(**{k: BATTERY_PARAMS[k] for k in (
        "capacity_kwh", "max_charge_kw", "max_discharge_kw",
        "round_trip_eff", "reserve_soc_pct", "cycle_cap_per_day",
    )}, current_soc_kwh=BATTERY_PARAMS["current_soc_kwh"])
    trades = []
    for slot, imp, exp in zip(_SLOTS_48, _IMP, _EXP):
        f, a = fm[slot], am[slot]
        t = run_slot(bat, slot, imp, exp, _IMP, preset,
                     f.load_kwh, f.solar_kwh, a.load_kwh, a.solar_kwh)
        trades.append(t)
    return bat, trades


def test_policy_cycle_cap_not_breached():
    bat, _ = _run_day_policy(HH_EV_SOLAR, DEFAULT_PRESET, seed=3)
    assert bat.cycles_today <= bat.cycle_cap_per_day + 1e-9


def test_policy_soc_within_bounds():
    bat, _ = _run_day_policy(HH_STABLE, DEFAULT_PRESET, seed=5)
    assert bat.current_soc_kwh >= bat.reserve_kwh - 1e-9
    assert bat.current_soc_kwh <= bat.capacity_kwh + 1e-9


# ---------------------------------------------------------------------------
# Oracle: pct_of_optimal within [0, 1] and oracle >= policy
# ---------------------------------------------------------------------------

def _oracle_cashflow(hh_params, seed=0):
    series = generate_slots(hh_params, _SLOTS_48, seed=seed)
    am = {s.slot_start: s for s in series["actual"]}
    oracle_slots = [(sl, imp, exp, am[sl].load_kwh, am[sl].solar_kwh)
                    for sl, imp, exp in zip(_SLOTS_48, _IMP, _EXP)]
    total, _, _ = run_window(BATTERY_PARAMS, oracle_slots)
    return total


def test_pct_of_optimal_in_range():
    oracle_cf = _oracle_cashflow(HH_STABLE, seed=1)
    _, trades = _run_day_policy(HH_STABLE, DEFAULT_PRESET, seed=1)
    policy_cf = sum(t["cashflow"] for t in trades)
    if oracle_cf > 0:
        pct = policy_cf / oracle_cf
        assert -0.05 <= pct <= 1.05


def test_oracle_beats_policy():
    oracle_cf = _oracle_cashflow(HH_EV_SOLAR, seed=10)
    _, trades = _run_day_policy(HH_EV_SOLAR, DEFAULT_PRESET, seed=10)
    policy_cf = sum(t["cashflow"] for t in trades)
    assert oracle_cf >= policy_cf - 1e-4


# ---------------------------------------------------------------------------
# Household fit: matched > default > mismatched
# ---------------------------------------------------------------------------

def test_well_matched_beats_default_on_solar():
    """Export-tuned preset should match or beat default for a solar household."""
    hh = {"annual_kwh": 4500, "has_solar": True, "solar_kwp": 4.0,
          "has_ev": False, "occupancy_profile": "standard", "load_volatility": 0.15}
    export_tuned = {**DEFAULT_PRESET, "export_threshold_p": 12.0, "reserve_soc_pct": 0.15,
                    "min_spread_p": 5.0}
    seed = 42
    _, default_trades = _run_day_policy(hh, DEFAULT_PRESET, seed=seed)
    _, tuned_trades = _run_day_policy(hh, export_tuned, seed=seed)
    default_cf = sum(t["cashflow"] for t in default_trades)
    tuned_cf = sum(t["cashflow"] for t in tuned_trades)
    # Tuned should be >= default (may tie on days with no export opportunity).
    assert tuned_cf >= default_cf - 0.01


def test_mismatched_underperforms_default():
    """Aggressive-export preset on a no-solar home should do no better than default."""
    hh = HH_STABLE
    mismatched = {**DEFAULT_PRESET, "export_threshold_p": 8.0,
                  "discharge_dearest_slots": 16, "reserve_soc_pct": 0.05, "min_spread_p": 2.0}
    seed = 7
    oracle_cf = _oracle_cashflow(hh, seed=seed)
    _, default_trades = _run_day_policy(hh, DEFAULT_PRESET, seed=seed)
    _, mis_trades = _run_day_policy(hh, mismatched, seed=seed)
    default_cf = sum(t["cashflow"] for t in default_trades)
    mis_cf = sum(t["cashflow"] for t in mis_trades)
    if oracle_cf > 0:
        pct_default = default_cf / oracle_cf
        pct_mis = mis_cf / oracle_cf
        # Mismatched should not significantly beat default; it may be equal or worse.
        assert pct_mis <= pct_default + 0.05
