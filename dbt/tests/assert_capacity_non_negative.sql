-- Hard guardrail: battery capacity and state of charge are never negative.
select battery_id, capacity_kwh, current_soc_kwh
from {{ ref('stg_batteries') }}
where capacity_kwh < 0 or current_soc_kwh < 0
