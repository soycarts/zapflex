-- One row per customer. Skill metric: pct_of_optimal = captured savings (from trades)
-- over the perfect-hindsight optimum (from benchmarks, written by the oracle). Computed
-- once here so every consumer reads the same numbers.
with savings as (
    select customer_id, sum(cashflow) as captured_savings
    from {{ ref('stg_trades') }}
    group by 1
),

optimal as (
    select customer_id, sum(optimal_savings) as theoretical_optimal
    from {{ ref('stg_benchmarks') }}
    group by 1
),

capacity as (
    select customer_id, sum(capacity_kwh) as fleet_capacity_kwh
    from {{ ref('stg_batteries') }}
    group by 1
),

joined as (
    select
        c.customer_id,
        c.handle,
        c.region,
        coalesce(cap.fleet_capacity_kwh, 0) as fleet_capacity_kwh,
        coalesce(s.captured_savings, 0)     as captured_savings,
        coalesce(o.theoretical_optimal, 0)  as theoretical_optimal
    from {{ ref('stg_customers') }} c
    left join savings  s   on s.customer_id   = c.customer_id
    left join optimal  o   on o.customer_id   = c.customer_id
    left join capacity cap on cap.customer_id = c.customer_id
    where c.status = 'active'
)

select
    customer_id,
    handle,
    region,
    round(fleet_capacity_kwh, 4) as fleet_capacity_kwh,
    round(captured_savings, 4)   as captured_savings,
    round(theoretical_optimal, 4) as theoretical_optimal,
    case when theoretical_optimal > 0
         then round(captured_savings / theoretical_optimal, 4)
    end as pct_of_optimal,
    dense_rank() over (
        order by case when theoretical_optimal > 0
                      then captured_savings / theoretical_optimal
                      else -1 end desc
    ) as rank,
    now() as updated_at
from joined
