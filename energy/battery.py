"""Battery model: capacity, charge/discharge limits, efficiency, reserve SOC, cycle accounting."""
from dataclasses import dataclass, field


@dataclass
class Battery:
    capacity_kwh: float
    max_charge_kw: float
    max_discharge_kw: float
    round_trip_eff: float = 0.90
    reserve_soc_pct: float = 0.10
    cycle_cap_per_day: float = 1.5
    current_soc_kwh: float = 0.0
    cycles_today: float = 0.0

    @property
    def reserve_kwh(self) -> float:
        return self.capacity_kwh * self.reserve_soc_pct

    @property
    def usable_kwh(self) -> float:
        return self.capacity_kwh - self.reserve_kwh

    @property
    def soc_pct(self) -> float:
        return self.current_soc_kwh / self.capacity_kwh

    def _cycles_remaining(self) -> float:
        return max(0.0, self.cycle_cap_per_day - self.cycles_today)

    def charge(self, slot_hours: float = 0.5) -> float:
        """Charge for one slot. Returns energy actually drawn from grid (kWh)."""
        max_energy_kw = self.max_charge_kw * slot_hours
        headroom = self.capacity_kwh - self.current_soc_kwh
        # energy stored in battery (after efficiency loss on the way in)
        max_stored = min(max_energy_kw * self.round_trip_eff, headroom)
        # cycle cap: a full charge = 0.5 cycles
        max_by_cycle = self._cycles_remaining() * self.capacity_kwh * 0.5
        stored = min(max_stored, max_by_cycle)
        if stored <= 0:
            return 0.0
        grid_drawn = stored / self.round_trip_eff
        self.current_soc_kwh += stored
        self.cycles_today += stored / self.capacity_kwh
        return grid_drawn

    def discharge(self, slot_hours: float = 0.5) -> float:
        """Discharge for one slot. Returns energy delivered to home/grid (kWh)."""
        max_energy_kw = self.max_discharge_kw * slot_hours
        available = self.current_soc_kwh - self.reserve_kwh
        max_by_cycle = self._cycles_remaining() * self.capacity_kwh * 0.5
        delivered = min(max_energy_kw, available, max_by_cycle)
        if delivered <= 0:
            return 0.0
        self.current_soc_kwh -= delivered
        self.cycles_today += delivered / self.capacity_kwh
        return delivered

    def reset_daily_cycles(self) -> None:
        self.cycles_today = 0.0

    def snapshot(self) -> dict:
        return {
            "soc_kwh": round(self.current_soc_kwh, 4),
            "soc_pct": round(self.soc_pct, 4),
            "cycles_today": round(self.cycles_today, 4),
        }
