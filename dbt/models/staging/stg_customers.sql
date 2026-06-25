select
    id as customer_id,
    handle,
    region,
    status,
    revenue_share_pct,
    sim_joined_at
from {{ source('public', 'customers') }}
