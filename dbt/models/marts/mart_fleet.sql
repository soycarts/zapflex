-- Fleet capability: one total row (region null) plus per-region rows. flexible_kw is
-- the dispatchable power right now (sum of max discharge); available_shift_kwh is the
-- energy we can move now (SOC above each battery's reserve floor).
with bat as (
    select
        c.region,
        b.customer_id,
        b.capacity_kwh,
        b.max_discharge_kw,
        greatest(b.current_soc_kwh - b.reserve_soc_pct * b.capacity_kwh, 0) as shiftable_kwh
    from {{ ref('stg_batteries') }} b
    join {{ ref('stg_customers') }} c
        on c.customer_id = b.customer_id and c.status = 'active'
),

per_region as (
    select
        region,
        sum(capacity_kwh)     as total_capacity_kwh,
        sum(max_discharge_kw) as flexible_kw,
        sum(shiftable_kwh)    as available_shift_kwh,
        count(distinct customer_id) as customer_count
    from bat
    group by 1
),

total as (
    select
        cast(null as text) as region,
        coalesce(sum(capacity_kwh), 0)     as total_capacity_kwh,
        coalesce(sum(max_discharge_kw), 0) as flexible_kw,
        coalesce(sum(shiftable_kwh), 0)    as available_shift_kwh,
        count(distinct customer_id)        as customer_count
    from bat
),

unioned as (
    select region, total_capacity_kwh, flexible_kw, available_shift_kwh, customer_count from total
    union all
    select region, total_capacity_kwh, flexible_kw, available_shift_kwh, customer_count from per_region
)

select
    region,
    round(total_capacity_kwh, 4)  as total_capacity_kwh,
    round(flexible_kw, 4)         as flexible_kw,
    round(available_shift_kwh, 4) as available_shift_kwh,
    customer_count,
    now() as updated_at
from unioned
order by region nulls first
