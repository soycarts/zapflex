"""Per-slot household load and solar: a smooth forecast and a noisy actual.

The policy sees only the forecast. Settlement and the oracle use the actual.
The gap between them is where the strategy levers earn their keep.

Actual series includes:
  - Multiplicative Gaussian noise on load, scaled by load_volatility.
  - EV charging spikes (has_ev=True): ~20% of days, 3-6 slots at 1.5-2.5 kWh each.
  - Solar weather variation (has_solar=True): ~25% of days heavily overcast.
"""
import math
import random
import datetime
from typing import NamedTuple


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

    # Build per-day actual modifiers (EV spike slots, solar weather factor).
    days = sorted({s[:10] for s in slots})
    day_mods: dict[str, dict] = {}
    for day in days:
        ev_slots: set[int] = set()
        if has_ev and rng.random() < 0.20:
            start = rng.randint(34, 42)           # 17:00–21:00
            length = rng.randint(3, 6)
            ev_slots = set(range(start, start + length))

        solar_factor = 1.0
        if has_solar:
            r = rng.random()
            if r < 0.25:
                solar_factor = rng.uniform(0.05, 0.25)   # overcast
            else:
                solar_factor = rng.uniform(0.85, 1.10)   # normal variation

        day_mods[day] = {"ev_slots": ev_slots, "solar_factor": solar_factor}

    forecast: list[SlotSeries] = []
    actual: list[SlotSeries] = []

    for slot in slots:
        si = _slot_index(slot)
        doy = _day_of_year(slot)
        day = slot[:10]
        mods = day_mods[day]

        f_load = _forecast_load(annual_kwh, si)
        f_solar = _forecast_solar(solar_kwp, si, doy) if has_solar else 0.0

        # Actual: multiplicative noise on load.
        noise = rng.gauss(0.0, volatility)
        a_load = max(0.0, f_load * (1.0 + noise))
        if si in mods["ev_slots"]:
            a_load += rng.uniform(1.5, 2.5)          # EV charging spike kWh

        a_solar = f_solar * mods["solar_factor"] if has_solar else 0.0

        forecast.append(SlotSeries(slot, round(f_load, 4), round(f_solar, 4)))
        actual.append(SlotSeries(slot, round(a_load, 4), round(a_solar, 4)))

    return {"forecast": forecast, "actual": actual}
