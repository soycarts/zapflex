-- One-row support snapshot for the dashboard. Always emits a row (zeros/nulls on an
-- empty ticket table).
select
    coalesce(sum(case when status = 'open' then 1 else 0 end), 0)      as open_tickets,
    coalesce(sum(case when status = 'escalated' then 1 else 0 end), 0) as escalated,
    avg(case when answered_at is not null
             then extract(epoch from (answered_at - created_at)) end)  as avg_response_secs,
    max(case when status in ('open', 'escalated')
             then extract(epoch from (now() - created_at)) end)        as oldest_open_age_secs,
    now() as updated_at
from {{ ref('stg_support_tickets') }}
