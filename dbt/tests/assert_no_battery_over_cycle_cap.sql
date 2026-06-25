-- Hard guardrail: no battery exceeds its cycle cap on any sim day. Grouped on the
-- LOCAL (Europe/London) day via stg_trades.london_day — grouping by UTC would split
-- midnight-BST slots across days and misreport a false breach. Tolerance covers the
-- per-slot rounding the optimiser already bounds (max ~1.5002 cycles/day).
select
    t.battery_id,
    t.london_day,
    sum(t.cycles_used) as cycles_used,
    b.cycle_cap_per_day
from {{ ref('stg_trades') }} t
join {{ ref('stg_batteries') }} b on b.battery_id = t.battery_id
group by t.battery_id, t.london_day, b.cycle_cap_per_day
having sum(t.cycles_used) > b.cycle_cap_per_day + 0.01
