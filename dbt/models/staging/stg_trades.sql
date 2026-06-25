-- Settled trades, tagged with the local (Europe/London) sim day. Per-day aggregations
-- (cycle cap, daily P&L) must group on london_day, not the UTC date: Agile slots carry
-- a BST/GMT offset, so UTC grouping splits midnight-BST slots and misreports the day.
select
    id,
    battery_id,
    customer_id,
    sim_time,
    (sim_time at time zone 'Europe/London')::date as london_day,
    action,
    energy_kwh,
    price_p_per_kwh,
    cashflow,
    cycles_used
from {{ source('public', 'trades') }}
