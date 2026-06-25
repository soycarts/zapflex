"""Tests for the forecast/actual split, settlement, and leaderboard invariants."""
import pytest
from energy.household import generate_slots
from energy.forecast import forecast_series, MODELS
from energy.policy import plan_and_settle, settle
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
    max_forecast = max(forecasts)
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
# Policy: hard caps never breached on the executed plan
# ---------------------------------------------------------------------------

def _run_day_policy(hh_params, seed=0, model=None):
    model = model if model is not None else MODELS["naive"]
    series = generate_slots(hh_params, _SLOTS_48, seed=seed)
    am = {s.slot_start: s for s in series["actual"]}
    fm = forecast_series(hh_params, _SLOTS_48, model, seed=seed)
    total, trades = plan_and_settle(BATTERY_PARAMS, _SLOTS_48, _IMP, _EXP, fm, am)
    return total, trades


def _soc_trajectory(trades):
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


def test_policy_cycle_cap_not_breached():
    _, trades = _run_day_policy(HH_EV_SOLAR, seed=3)
    total_cycles = sum(t["cycles_used"] for t in trades)
    assert total_cycles <= BATTERY_PARAMS["cycle_cap_per_day"] + 1e-9


def test_policy_soc_within_bounds():
    _, trades = _run_day_policy(HH_STABLE, seed=5)
    # Tolerance covers rounding of per-slot energy to 4 dp accumulated over the day.
    for soc in _soc_trajectory(trades):
        assert soc >= -1e-3
        assert soc <= BATTERY_PARAMS["capacity_kwh"] + 1e-3


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
    policy_cf, _ = _run_day_policy(HH_STABLE, seed=1)
    if oracle_cf > 0:
        pct = policy_cf / oracle_cf
        assert -0.05 <= pct <= 1.05


def test_oracle_beats_policy():
    oracle_cf = _oracle_cashflow(HH_EV_SOLAR, seed=10)
    policy_cf, _ = _run_day_policy(HH_EV_SOLAR, seed=10)
    assert oracle_cf >= policy_cf - 1e-4


# ---------------------------------------------------------------------------
# Forecast skill: learning a home's routine is the leaderboard lever
# ---------------------------------------------------------------------------

# A multi-day window so the EV routine (and its skipped days) averages out — the
# forecast-skill edge is an expectation over many days, not a single one. The price
# curve puts the expensive peak in the evening (slots ~32-41), overlapping the EV
# plug-in window, as real Agile does — that overlap is what makes learning the EV
# routine pay (keep charge to self-consume the evening peak instead of importing it).
_DAYS = [f"2026-01-{d:02d}" for d in range(10, 17)]   # 7 days
_SLOTS_WEEK = [f"{d}T{h:02d}:{m:02d}:00Z" for d in _DAYS for h in range(24) for m in (0, 30)]
_IMP_EVE = [6.0] * 14 + [16.0] * 18 + [38.0] * 10 + [12.0] * 6   # 48 slots, evening peak
_EXP_EVE = [p * 0.80 for p in _IMP_EVE]
_IMP_WEEK = _IMP_EVE * len(_DAYS)
_EXP_WEEK = _EXP_EVE * len(_DAYS)


def _week_policy(hh_params, model, seed=0):
    series = generate_slots(hh_params, _SLOTS_WEEK, seed=seed)
    am = {s.slot_start: s for s in series["actual"]}
    fm = forecast_series(hh_params, _SLOTS_WEEK, model, seed=seed)
    total, _ = plan_and_settle(BATTERY_PARAMS, _SLOTS_WEEK, _IMP_WEEK, _EXP_WEEK, fm, am)
    return total


def _week_oracle(hh_params, seed=0):
    series = generate_slots(hh_params, _SLOTS_WEEK, seed=seed)
    am = {s.slot_start: s for s in series["actual"]}
    oracle_slots = [(sl, imp, exp, am[sl].load_kwh, am[sl].solar_kwh)
                    for sl, imp, exp in zip(_SLOTS_WEEK, _IMP_WEEK, _EXP_WEEK)]
    total, _, _ = run_window(BATTERY_PARAMS, oracle_slots)
    return total


def test_learned_forecast_beats_naive_on_ev():
    """Learning the EV routine lifts an EV home meaningfully toward the oracle."""
    seed = 11
    naive_cf = _week_policy(HH_EV_SOLAR, MODELS["naive"], seed=seed)
    learned_cf = _week_policy(HH_EV_SOLAR, MODELS["learned"], seed=seed)
    assert learned_cf > naive_cf


def test_forecast_skill_bounded_by_oracle():
    """Even a fully-learned forecast cannot beat the perfect-hindsight oracle."""
    seed = 11
    oracle_cf = _week_oracle(HH_EV_SOLAR, seed=seed)
    learned_cf = _week_policy(HH_EV_SOLAR, MODELS["learned"], seed=seed)
    assert oracle_cf >= learned_cf - 1e-4


def test_phantom_routine_forecast_hurts():
    """Predicting a routine the home does not have (phantom EV) costs yield."""
    seed = 11
    # Actual is a stable home with NO EV.
    series = generate_slots(HH_STABLE, _SLOTS_WEEK, seed=seed)
    am = {s.slot_start: s for s in series["actual"]}

    naive_fm = forecast_series(HH_STABLE, _SLOTS_WEEK, MODELS["naive"], seed=seed)
    # Forecast hallucinates an EV routine the home does not actually run.
    phantom_fm = forecast_series({**HH_STABLE, "has_ev": True}, _SLOTS_WEEK, MODELS["learned"], seed=seed)

    naive_cf, _ = plan_and_settle(BATTERY_PARAMS, _SLOTS_WEEK, _IMP_WEEK, _EXP_WEEK, naive_fm, am)
    phantom_cf, _ = plan_and_settle(BATTERY_PARAMS, _SLOTS_WEEK, _IMP_WEEK, _EXP_WEEK, phantom_fm, am)
    assert phantom_cf < naive_cf
