"""Simulated per-slot household load and optional solar generation."""
import math


# Half-hour slot index (0=midnight, 47=23:30) → load fraction of daily total.
# Standard occupancy profile: morning and evening peaks, low overnight.
_STANDARD_PROFILE = [
    0.020, 0.018, 0.016, 0.015, 0.015, 0.016,  # 00:00–02:30
    0.018, 0.022, 0.032, 0.038, 0.034, 0.030,  # 03:00–05:30
    0.028, 0.026, 0.025, 0.024, 0.024, 0.025,  # 06:00–08:30
    0.030, 0.032, 0.030, 0.028, 0.026, 0.025,  # 09:00–11:30
    0.024, 0.023, 0.023, 0.024, 0.025, 0.026,  # 12:00–14:30
    0.028, 0.030, 0.033, 0.038, 0.044, 0.050,  # 15:00–17:30
    0.055, 0.058, 0.056, 0.052, 0.048, 0.044,  # 18:00–20:30
    0.040, 0.036, 0.032, 0.028, 0.025, 0.022,  # 21:00–23:30
]
assert abs(sum(_STANDARD_PROFILE) - 1.0) < 1e-6, "profile must sum to 1"


def slot_load_kwh(
    annual_kwh: float,
    slot_index: int,
    occupancy_profile: str = "standard",
) -> float:
    """Return expected load for a 30-minute slot (kWh)."""
    daily_kwh = annual_kwh / 365.0
    fractions = _STANDARD_PROFILE  # only one profile for now
    return daily_kwh * fractions[slot_index % 48]


def slot_solar_kwh(
    solar_kwp: float,
    slot_index: int,
    day_of_year: int = 180,
) -> float:
    """Estimate solar generation for a 30-minute slot (kWh).

    Uses a simple sinusoidal daylight curve peaking at solar noon (slot 24).
    Seasonal variation via day_of_year (1–365); UK peak irradiance ~0.8 kW/kWp.
    """
    if solar_kwp <= 0:
        return 0.0
    # Sunrise ~slot 12 (06:00) to sunset ~slot 36 (18:00) on a summer day.
    # Shift with season: winter day is shorter.
    season_factor = 0.5 + 0.5 * math.sin(2 * math.pi * (day_of_year - 80) / 365)
    half_day = 12 * season_factor  # slots either side of noon
    noon_slot = 24
    angle = math.pi * (slot_index - noon_slot) / (2 * half_day) if half_day > 0 else math.pi
    irradiance = max(0.0, math.cos(angle))
    peak_kw = solar_kwp * 0.8 * season_factor
    return peak_kw * irradiance * 0.5  # 0.5 hour slot
