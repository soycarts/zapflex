-- Company money entries (revenue_share | grid_services | cost), signed in GBP,
-- tagged with the local sim day for the per-day P&L rows.
select
    id,
    customer_id,
    sim_time,
    (sim_time at time zone 'Europe/London')::date as london_day,
    entry_type,
    amount
from {{ source('public', 'ledger') }}
