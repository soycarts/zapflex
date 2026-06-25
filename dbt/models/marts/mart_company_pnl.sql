-- Company P&L from the ledger (written by the Finance agent). One total row
-- (sim_day null) plus per-sim-day rows. Ledger amounts are signed company money;
-- cost entries are negative, so net = revenue_share + grid_services - costs equals
-- the sum of all amounts. Always emits the total row, even on an empty ledger.
with day_agg as (
    select
        london_day as sim_day,
        sum(case when entry_type = 'revenue_share' then amount else 0 end) as revenue_share,
        sum(case when entry_type = 'grid_services' then amount else 0 end) as grid_services,
        sum(case when entry_type = 'cost'          then -amount else 0 end) as costs,
        count(distinct customer_id) as customer_count
    from {{ ref('stg_ledger') }}
    group by 1
),

total as (
    select
        cast(null as date) as sim_day,
        coalesce(sum(case when entry_type = 'revenue_share' then amount else 0 end), 0) as revenue_share,
        coalesce(sum(case when entry_type = 'grid_services' then amount else 0 end), 0) as grid_services,
        coalesce(sum(case when entry_type = 'cost'          then -amount else 0 end), 0) as costs,
        (select count(*) from {{ ref('stg_customers') }} where status = 'active') as customer_count
    from {{ ref('stg_ledger') }}
),

unioned as (
    select sim_day, revenue_share, grid_services, costs, customer_count from total
    union all
    select sim_day, revenue_share, grid_services, costs, customer_count from day_agg
)

select
    sim_day,
    round(revenue_share, 4) as revenue_share,
    round(grid_services, 4) as grid_services,
    round(costs, 4)         as costs,
    round(revenue_share + grid_services - costs, 4) as net,
    customer_count,
    now() as updated_at
from unioned
order by sim_day nulls first
