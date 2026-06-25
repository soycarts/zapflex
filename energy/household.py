"""Per-slot household load and solar: the ground-truth ACTUAL series.

This module generates what the home actually does. The naive forecast it also
returns (profile + clear-sky solar, no EV) is the knowledge-0 baseline; richer,
"learned" forecasts live in energy/forecast.py. Settlement and the oracle use the
actual; the policy plans on whichever forecast the agent has built. The gap between
them is forecast error — and how much of it the agent can close is the game.

The actual splits into a LEARNABLE routine and UNLEARNABLE noise, so forecast
skill (energy/forecast.py) has something real to capture but can never hit 100%:
  - Load: profile × annual_kwh × multiplicative Gaussian noise (unlearnable).
  - EV (has_ev): a consistent daily evening charging routine — same window and
    baseline kWh every day (learnable) — plus magnitude noise, ~15% skipped days,
    and rare daytime top-ups (unlearnable).
  - Solar (has_solar): clear-sky × a daily weather factor scattered around a fixed
    climatological mean (the mean is learnable; the daily scatter is not).
"""
import math
import random
import datetime
import zlib
from typing import NamedTuple


def stable_seed(name: str) -> int:
    """Deterministic per-customer seed (process-independent, unlike hash()).

    The household routine must be reproducible across runs so the leaderboard is
    stable and an agent can learn a fixed pattern."""
    return zlib.crc32(name.encode()) & 0xFFFF

# Climatological mean of the daily solar weather factor (learnable by a seasonal
# forecast); the daily scatter around it is the unlearnable part.
SOLAR_CLIMATOLOGY = 0.78
# Fraction of days the EV actually follows its routine (the rest are skipped).
EV_ROUTINE_PROB = 0.85


def ev_routine(seed: int) -> tuple[int, int, float]:
    """The household's consistent EV charging routine (the learnable pattern):
    (start_slot, length_slots, base_kwh_per_slot). Deterministic per household so
    energy/forecast.py can learn the same routine the actual is generated from."""
    r = random.Random(f"ev-{seed}")
    start = r.randint(36, 40)        # consistent plug-in, 18:00–20:00
    length = r.randint(4, 6)         # charges for 2–3 hours
    base = r.uniform(2.0, 3.0)       # baseline kWh per slot
    return start, length, base


# Half-hour slot index (0 = midnight, 47 = 23:30) → fraction of daily load.
# Standard occupancy profile: morning and evening peaks, low overnight. Sums to 1.0.
_STANDARD_PROFILE = [
    0.013689, 0.012320, 0.010951, 0.010267, 0.010267, 0.010951,  # 00:00–02:30
    0.012320, 0.015058, 0.021903, 0.026010, 0.023272, 0.020534,  # 03:00–05:30
    0.019165, 0.017796, 0.017112, 0.016427, 0.016427, 0.017112,  # 06:00–08:30
    0.020534, 0.021903, 0.020534, 0.019165, 0.017796, 0.017112,  # 09:00–11:30
    0.016427, 0.015743, 0.015743, 0.016427, 0.017112, 0.017796,  # 12:00–14:30
    0.019165, 0.020534, 0.022587, 0.026010, 0.030116, 0.034223,  # 15:00–17:30
    0.037645, 0.039699, 0.038330, 0.035592, 0.032854, 0.030116,  # 18:00–20:30
    0.027379, 0.024641, 0.021903, 0.019165, 0.017112, 0.015058,  # 21:00–23:30
]
assert abs(sum(_STANDARD_PROFILE) - 1.0) < 1e-4


class SlotSeries(NamedTuple):
    slot_start: str
    load_kwh: float
    solar_kwh: float

    @property
    def net_load_kwh(self) -> float:
        return self.load_kwh - self.solar_kwh


def _slot_index(iso_time: str) -> int:
    dt = datetime.datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    return dt.hour * 2 + (1 if dt.minute >= 30 else 0)


def _day_of_year(iso_time: str) -> int:
    dt = datetime.datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    return dt.timetuple().tm_yday


def _forecast_load(annual_kwh: float, slot_index: int) -> float:
    return (annual_kwh / 365.0) * _STANDARD_PROFILE[slot_index % 48]


def _forecast_solar(solar_kwp: float, slot_index: int, day_of_year: int) -> float:
    if solar_kwp <= 0:
        return 0.0
    season = 0.5 + 0.5 * math.sin(2 * math.pi * (day_of_year - 80) / 365)
    half_day = 12 * season
    if half_day <= 0:
        return 0.0
    angle = math.pi * (slot_index - 24) / (2 * half_day)
    irradiance = max(0.0, math.cos(angle))
    return solar_kwp * 0.8 * season * irradiance * 0.5  # 0.5-hour slot


def generate_slots(
    params: dict,
    slots: list[str],
    seed: int = 0,
) -> dict[str, list[SlotSeries]]:
    """Generate forecast and actual load/solar for every sim slot.

    params: dict with keys annual_kwh, has_solar, solar_kwp, has_ev,
            occupancy_profile, load_volatility (from the households row).
    slots:  sorted list of slot_start ISO strings.
    seed:   use customer_id so each customer gets a unique deterministic series.

    Returns {'forecast': [SlotSeries, ...], 'actual': [SlotSeries, ...]}.
    """
    annual_kwh = float(params.get("annual_kwh", 3500))
    has_solar = bool(params.get("has_solar", False))
    solar_kwp = float(params.get("solar_kwp", 0))
    has_ev = bool(params.get("has_ev", False))
    volatility = float(params.get("load_volatility", 0.15))

    rng = random.Random(seed)
    ev_start, ev_len, ev_base = ev_routine(seed) if has_ev else (0, 0, 0.0)

    # Build per-day actual modifiers: the EV charge for the day and the solar weather.
    days = sorted({s[:10] for s in slots})
    day_mods: dict[str, dict] = {}
    for day in days:
        ev_load: dict[int, float] = {}
        if has_ev:
            # The routine: same window every day, magnitude scattered around base.
            if rng.random() < EV_ROUTINE_PROB:
                mag = max(0.0, ev_base * (1.0 + rng.gauss(0.0, 0.30)))
                for k in range(ev_len):
                    ev_load[(ev_start + k) % 48] = mag
            # Unlearnable: a rare daytime top-up charge.
            if rng.random() < 0.15:
                ds = rng.randint(20, 28)
                for k in range(rng.randint(2, 4)):
                    si = (ds + k) % 48
                    ev_load[si] = ev_load.get(si, 0.0) + rng.uniform(2.0, 4.0)

        # Daily solar weather factor scattered around the climatological mean.
        solar_factor = 1.0
        if has_solar:
            solar_factor = max(0.05, rng.gauss(SOLAR_CLIMATOLOGY, 0.30))

        day_mods[day] = {"ev_load": ev_load, "solar_factor": solar_factor}

    forecast: list[SlotSeries] = []
    actual: list[SlotSeries] = []

    for slot in slots:
        si = _slot_index(slot)
        doy = _day_of_year(slot)
        day = slot[:10]
        mods = day_mods[day]

        f_load = _forecast_load(annual_kwh, si)
        f_solar = _forecast_solar(solar_kwp, si, doy) if has_solar else 0.0

        # Actual: multiplicative noise on load, plus the day's EV charge.
        noise = rng.gauss(0.0, volatility)
        a_load = max(0.0, f_load * (1.0 + noise)) + mods["ev_load"].get(si, 0.0)
        a_solar = f_solar * mods["solar_factor"] if has_solar else 0.0

        # Naive (knowledge-0) forecast: profile + clear-sky solar, no EV.
        forecast.append(SlotSeries(slot, round(f_load, 4), round(f_solar, 4)))
        actual.append(SlotSeries(slot, round(a_load, 4), round(a_solar, 4)))

    return {"forecast": forecast, "actual": actual}
