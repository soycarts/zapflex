-- Guardrail: the skill metric must lie in [0, 1]. The oracle is a provable upper
-- bound on the policy, so a value above 1 (or below 0) means a broken benchmark.
-- A small tolerance absorbs rounding.
select customer_id, pct_of_optimal
from {{ ref('mart_leaderboard') }}
where pct_of_optimal is not null
  and (pct_of_optimal < -0.001 or pct_of_optimal > 1.001)
