"""Household forecast models: the skill layer the agents tune per customer.

The Agile price curve is fully visible, so price-side optimisation is already
maxed out. The only thing left to predict is the household itself — and that is
where the leaderboard skill lives. A forecast model captures some fraction of the
home's learnable routine (energy/household.py): its EV charging schedule and its
solar climatology. Two knowledge knobs, each in [0, 1]:

  - solar_knowledge: 0 assumes optimistic clear-sky solar; 1 de-rates to the
    climatological mean. Helps only solar homes.
  - ev_knowledge:    0 ignores the EV; 1 anticipates the full evening routine.
    Helps only homes that actually charge an EV — and a model that predicts a
    routine the home does not have will plan for phantom load and lose yield.

Better knowledge → forecast closer to actual → higher pct_of_optimal, bounded
below 100% by the unlearnable noise. The trading agent's job is to learn each
home's routine and raise its forecast model up this curve over time.

Named models are the customer-facing presets; the continuous knobs sit underneath
so the agent can fine-tune within a band.
"""
from __future__ import annotations

from energy.household import (
    SlotSeries, ev_routine, SOLAR_CLIMATOLOGY, EV_ROUTINE_PROB,
    _slot_index, _day_of_year, _forecast_load, _forecast_solar,
)

# Named forecast models → (solar_knowledge, ev_knowledge).
MODELS: dict[str, dict] = {
    "naive":    {"solar_knowledge": 0.0, "ev_knowledge": 0.0},
    "seasonal": {"solar_knowledge": 1.0, "ev_knowledge": 0.0},
    "learned":  {"solar_knowledge": 1.0, "ev_knowledge": 1.0},
}


def forecast_series(
    params: dict,
    slots: list[str],
    model: dict,
    seed: int = 0,
) -> dict[str, SlotSeries]:
    """Build a forecast {slot_start: SlotSeries} for a household under a model.

    params: the household row (annual_kwh, has_solar, solar_kwp, has_ev, ...).
    model:  {solar_knowledge, ev_knowledge} in [0, 1].
    seed:   the household seed, so a 'learned' model reconstructs the same EV
            routine that energy/household.py generated the actual from.
    """
    annual_kwh = float(params.get("annual_kwh", 3500))
    has_solar = bool(params.get("has_solar", False))
    solar_kwp = float(params.get("solar_kwp", 0))
    has_ev = bool(params.get("has_ev", False))

    sk = max(0.0, min(1.0, float(model.get("solar_knowledge", 0.0))))
    ek = max(0.0, min(1.0, float(model.get("ev_knowledge", 0.0))))

    # Solar: interpolate clear-sky (knowledge 0) → climatological mean (knowledge 1).
    solar_factor = 1.0 - sk * (1.0 - SOLAR_CLIMATOLOGY)

    # EV: the expected routine load per window slot (base × how often it happens),
    # scaled by how much of the routine the model has learned.
    ev_start, ev_len, ev_base = ev_routine(seed) if has_ev else (0, 0, 0.0)
    ev_window = {(ev_start + k) % 48 for k in range(ev_len)}
    ev_expected = ev_base * EV_ROUTINE_PROB

    out: dict[str, SlotSeries] = {}
    for slot in slots:
        si = _slot_index(slot)
        doy = _day_of_year(slot)
        load = _forecast_load(annual_kwh, si)
        if has_ev and si in ev_window:
            load += ek * ev_expected
        solar = _forecast_solar(solar_kwp, si, doy) * solar_factor if has_solar else 0.0
        out[slot] = SlotSeries(slot, round(load, 4), round(solar, 4))
    return out
