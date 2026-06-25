select
    id,
    customer_id,
    status,
    priority,
    answered_by,
    created_at,
    answered_at
from {{ source('public', 'support_tickets') }}
