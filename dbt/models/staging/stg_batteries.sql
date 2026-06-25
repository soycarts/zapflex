select
    id as battery_id,
    customer_id,
    capacity_kwh,
    max_charge_kw,
    max_discharge_kw,
    reserve_soc_pct,
    cycle_cap_per_day,
    current_soc_kwh,
    strategy_preset
from {{ source('public', 'batteries') }}
