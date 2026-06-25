-- Guardrail: the ledger reconciles. Every P&L row's net must equal
-- revenue_share + grid_services - costs.
select sim_day, revenue_share, grid_services, costs, net
from {{ ref('mart_company_pnl') }}
where abs(net - (revenue_share + grid_services - costs)) > 0.005
