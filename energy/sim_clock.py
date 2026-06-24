"""Accelerated simulation clock: replays real Agile slots in order.

sim_time = slot_start of the Agile slot being replayed.
Default speed: 1 real second per simulated half-hour slot.
"""
import time
import datetime
from typing import Iterator


def slots_from_prices(prices: list[dict]) -> list[dict]:
    """Sort price rows by slot_start ascending, return as list."""
    return sorted(prices, key=lambda r: r["slot_start"])


class SimClock:
    """Yields (sim_time, import_price_p, export_price_p) for each slot in order."""

    def __init__(
        self,
        import_prices: list[dict],
        export_prices: list[dict],
        seconds_per_slot: float = 1.0,
    ) -> None:
        self.seconds_per_slot = seconds_per_slot
        # Build lookup: slot_start (str) -> price for each direction.
        self._import = {r["slot_start"]: float(r["price_p_per_kwh"]) for r in import_prices}
        self._export = {r["slot_start"]: float(r["price_p_per_kwh"]) for r in export_prices}
        # Sorted list of slot_starts that have both import and export prices.
        self._slots = sorted(set(self._import) & set(self._export))

    def __len__(self) -> int:
        return len(self._slots)

    def run(self, start_index: int = 0, num_slots: int | None = None) -> Iterator[tuple]:
        """Yield (slot_start_str, import_p, export_p), sleeping between slots.

        slot_start_str is the ISO string from the price data (this is sim_time).
        """
        slots = self._slots[start_index:]
        if num_slots is not None:
            slots = slots[:num_slots]
        for slot in slots:
            yield slot, self._import[slot], self._export[slot]
            time.sleep(self.seconds_per_slot)

    def iter_fast(self, start_index: int = 0, num_slots: int | None = None) -> Iterator[tuple]:
        """Yield without sleeping — for backtests and unit tests."""
        slots = self._slots[start_index:]
        if num_slots is not None:
            slots = slots[:num_slots]
        for slot in slots:
            yield slot, self._import[slot], self._export[slot]

    def slot_count_for_days(self, days: int) -> int:
        return days * 48
