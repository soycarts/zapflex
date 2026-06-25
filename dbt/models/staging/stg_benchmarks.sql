-- Oracle per-customer-per-day optima, tagged with the local sim day.
select
    id,
    customer_id,
    window_start,
    window_end,
    (window_start at time zone 'Europe/London')::date as london_day,
    optimal_savings
from {{ source('public', 'benchmarks') }}
