# SPEC.md — zapflex

This is the authoritative design for zapflex. Decisions live here. When a decision changes, update this file and nowhere else. CLAUDE.md points here for detail.

---

## 1. Thesis

zapflex is an open, hardware-agnostic, supplier-agnostic home-battery flexibility network, built as a self-running company for the Cursor Hands Off hackathon.

The value is two layers:
- Product layer (unlicensed): optimise home batteries against dynamic tariffs (Octopus Agile import and Agile Outgoing export). This is the behind-the-meter saving that needs no licence.
- Revenue layer (the real money): aggregate the optimised fleet and earn grid-services and flexibility revenue. At scale this runs through a Virtual Lead Party (VLP) or our own licence.

Go-to-market wedge: a gamified, tunable "personal trader" and a skill-normalised leaderboard, aimed at the prosumer and enthusiast segment that Octopus's Octoplus underserves (already-flexible homes benefit less from Saving Sessions). The game is a cheap-acquisition, high-retention engine for recruiting flexible assets.

Positioning: open and agnostic against Tesla (hardware lock-in) and Octopus (supplier-bound). Deeper and tunable against Predbat (free, powerful, technical, no social layer).

Strategic truth to defend: the optimiser was never the hard part. Running the company around it is. That is what the swarm does. Revenue is simulated and comes from the aggregation slice, never a fantasy subscription.

---

## 2. Repo layout

```
zapflex/
  README.md
  CLAUDE.md
  AGENTS.md                 # mirror of conventions for Cursor (or .cursor/rules/)
  .env.example
  task.py                   # the task CLI
  docs/
    SPEC.md                 # this file
  schema/                   # canonical Supabase DDL (operational tables)
    001_tasks.sql
    002_operational.sql
  dbt/
    models/
      staging/
      marts/                # mart_leaderboard, mart_company_pnl, mart_fleet, mart_support
      schema.yml            # dbt tests = data contracts + guardrails
  energy/                   # pre-built domain core (prep days)
    ingest_agile.py         # Octopus Agile import + Agile Outgoing -> DuckDB + tariff_prices
    battery.py              # battery model
    policy.py               # strategy executor: runs each customer's strategy per slot
    optimizer.py            # benchmark oracle: perfect-hindsight optimum
    household.py            # simulated per-slot load and optional solar
    sim_clock.py            # accelerated simulation clock (replays real Agile slots)
    backtest.py             # offline DuckDB backtest: baseline vs optimal
  agents/                   # the swarm
    runner.py               # the thin agent-runner (harness core)
    tools.py                # tool registry: DB reads/writes, the gate call
    gate.py                 # Telegram approval gate
    souls/                  # agent instruction files
      ceo.md
      trading.md
      growth.md
      support.md
      finance.md
  app/
    modal_app.py            # Modal: schedules, webhooks, the dbt heartbeat
  dashboard/                # live dashboard (reads marts + tasks)
  tests/                    # pytest for the domain core and the harness
```

---

## 3. Architecture

Two layers, one heartbeat.

- Operational layer: Supabase (Postgres) tables, written live by the agents. The source of truth.
- Analytical layer: dbt marts built on top of the operational tables. The dashboard and the agents read marts, never raw tables, for any derived figure.
- Heartbeat: agents write state continuously. A scheduled Modal job reruns dbt every 30 to 60 seconds (or only the changed marts, invoked through the dbtRunner Python API to skip CLI cold starts) to refresh the marts. Everything reads the refreshed marts.

Data flow per cycle: the policy executor dispatches each battery for the current sim slot per the customer's strategy and writes `trades`; the oracle scores the achievable optimum for the leaderboard; finance books revenue into `ledger`; growth and support mutate `customers`, `connections`, `community_posts`, `support_tickets`; dbt rebuilds the marts; CEO reads the marts and writes `tasks`; high-stakes actions queue to `pending_approvals` and wait on Telegram.

### Tariff data

The data is the easy part. Octopus publishes the Agile prices through a public REST API that needs no account and no auth key.

- The products endpoint returns every half-hourly unit rate for a regional tariff code over a time range.
- Two directions on the same API: import from the Agile tariff, export from Agile Outgoing. Two-way price exposure for free.
- Region: London is the `C` regional code (the `-C` tariff-code variant).
- Source cadence: the 48 half-hourly rates for the next day publish around 4pm, set by the EPEX day-ahead auction.
- History: paginated, so several weeks pull in a handful of GET calls.
- Ingestion (`energy/ingest_agile.py`, prep): one GET loop, store 48 slots per day. Land it in DuckDB for the offline backtest, and seed the Supabase `tariff_prices` table for the live replay.

Units: tariff prices are pence per kWh (import inc VAT). Money fields (`trades.cashflow`, `ledger.amount`) are GBP. Convert pence to GBP by dividing by 100.

### Simulation clock

During the hands-off window the sim runs fast so the agents live through many days and the dashboard visibly ticks.

- Default: 1 real second = 1 simulated half-hour slot. A sim day (48 slots) is 48 real seconds. A 2.5-hour hands-off window covers roughly 180 sim days. Configurable in `sim_clock.py`.
- The sim replays the ingested real Agile slots in order at the configured speed. `sim_time` on operational rows is the `slot_start` of the slot being replayed, so the simulated days are real historical pricing days, separate from `created_at` (wall clock).
- The deterministic policy executor runs every sim slot and is cheap. The trading agent (LLM) supervises on a slower real-time cadence and does not make a per-slot LLM call. This keeps inference cost and latency sane.

---

## 4. Data contracts

These are the field names and types the agents must use. Do not invent columns. Operational tables are real Postgres DDL for `schema/002_operational.sql`. Marts are dbt-built; their column contract and grain are given.

`tasks` is defined in `schema/001_tasks.sql` (see the project setup). Key fields: `id`, `title`, `phase` (prep|live), `category`, `status` (todo|doing|blocked|done|cancelled), `created_by_type` (human|claude_code|agent), `created_by_name`, `assigned_to`, `completed_by_type`, `completed_by_name`, `result`, `source_ref`, `created_at`, `started_at`, `completed_at`.

### Operational tables

```sql
-- Reference data, seeded in prep from the Agile ingestion, read by the policy and oracle.
create table tariff_prices (
  id              bigint generated always as identity primary key,
  region          text not null,                  -- GSP region code, e.g. 'C' for London
  direction       text not null,                  -- import|export
  slot_start      timestamptz not null,           -- start of the real 30-minute slot
  price_p_per_kwh numeric not null,               -- pence per kWh (import inc VAT)
  created_at      timestamptz not null default now(),
  unique (region, direction, slot_start)
);

create table customers (
  id                bigint generated always as identity primary key,
  handle            text not null,
  region            text not null,             -- Agile GSP region code, e.g. 'C' for London
  import_tariff     text not null default 'AGILE',
  export_tariff     text,                       -- 'AGILE_OUTGOING' or null
  status            text not null default 'active',   -- active|churned
  acquisition_source text,
  revenue_share_pct numeric not null default 0.20,    -- our cut of customer savings
  sim_joined_at     timestamptz,
  created_at        timestamptz not null default now()
);

create table batteries (
  id                  bigint generated always as identity primary key,
  customer_id         bigint not null references customers(id),
  brand               text,
  capacity_kwh        numeric not null,
  max_charge_kw       numeric not null,
  max_discharge_kw    numeric not null,
  round_trip_eff      numeric not null default 0.90,
  reserve_soc_pct     numeric not null default 0.10,
  cycle_cap_per_day   numeric not null default 1.5,   -- hard over-cycle guard
  current_soc_kwh     numeric not null default 0,
  strategy_preset     jsonb,                          -- tunable strategy params (see section 10)
  created_at          timestamptz not null default now()
);

-- Onboarding: the customer connecting their battery from provider X to our API.
create table connections (
  id                bigint generated always as identity primary key,
  customer_id       bigint not null references customers(id),
  battery_id        bigint references batteries(id),
  provider          text not null,                     -- givenergy|tesla|sigenergy|solis
  status            text not null default 'pending',   -- pending|connected|error
  error_reason      text,
  connected_at      timestamptz,
  last_telemetry_at timestamptz,
  created_at        timestamptz not null default now()
);

-- Simulated household so savings are meaningful and vary per customer.
create table households (
  customer_id        bigint primary key references customers(id),
  annual_kwh         numeric not null default 3500,
  has_solar          boolean not null default false,
  solar_kwp          numeric not null default 0,
  occupancy_profile  text not null default 'standard',  -- shapes the load curve
  created_at         timestamptz not null default now()
);

-- Strategy history, so a customer tuning and climbing has provenance.
create table strategy_versions (
  id            bigint generated always as identity primary key,
  battery_id    bigint not null references batteries(id),
  version       int not null,
  params        jsonb not null,
  author_type   text not null,                          -- customer|agent
  sim_time      timestamptz,
  created_at    timestamptz not null default now()
);

create table trades (
  id               bigint generated always as identity primary key,
  battery_id       bigint not null references batteries(id),
  customer_id      bigint not null references customers(id),
  sim_time         timestamptz not null,              -- the replayed half-hour slot
  action           text not null,                     -- charge|discharge|idle
  energy_kwh       numeric not null default 0,
  price_p_per_kwh  numeric not null,                  -- pence/kWh (import or export) at that slot
  cashflow         numeric not null,                  -- signed GBP, customer saving/earning
  cycles_used      numeric not null default 0,        -- fractional cycles this action added
  created_at       timestamptz not null default now()
);

create table ledger (
  id               bigint generated always as identity primary key,
  customer_id      bigint references customers(id),   -- null for company-wide entries
  sim_time         timestamptz not null,
  entry_type       text not null,                     -- revenue_share|grid_services|cost
  amount           numeric not null,                  -- signed GBP, company money
  note             text,
  created_at       timestamptz not null default now()
);

create table support_tickets (
  id               bigint generated always as identity primary key,
  customer_id      bigint references customers(id),
  channel          text not null default 'discord',   -- discord|email|in_app
  subject          text,
  body             text not null,
  status           text not null default 'open',      -- open|answered|escalated|closed
  priority         int not null default 3,
  answered_by      text,                              -- agent name
  resolution       text,
  created_at       timestamptz not null default now(),
  answered_at      timestamptz
);

create table community_posts (
  id               bigint generated always as identity primary key,
  customer_id      bigint references customers(id),   -- null for agent posts
  author_type      text not null,                     -- agent|customer
  author_name      text,
  channel          text not null default 'discord',
  body             text not null,
  created_at       timestamptz not null default now()
);

create table pending_approvals (
  id               bigint generated always as identity primary key,
  requested_by     text not null,                     -- agent name
  action_type      text not null,                     -- spend|pricing_change|external_send|licensing|other
  payload          jsonb not null,                    -- the proposed action
  status           text not null default 'pending',   -- pending|approved|rejected
  telegram_msg_id  text,
  resolved_by      text,
  created_at       timestamptz not null default now(),
  resolved_at      timestamptz
);

create table decisions_log (
  id               bigint generated always as identity primary key,
  agent            text not null,
  cycle            int,
  sim_time         timestamptz,
  action           text not null,
  rationale        text,
  state_summary    jsonb,
  tasks_created    jsonb,
  approvals_requested jsonb,
  created_at       timestamptz not null default now()
);

create table kill_switch (
  id               int primary key default 1,
  halted           boolean not null default false,
  reason           text,
  updated_at       timestamptz not null default now(),
  constraint single_row check (id = 1)
);
insert into kill_switch (id, halted) values (1, false);
```

### Marts (dbt)

`mart_leaderboard` — grain: one row per customer.
- customer_id (bigint), handle (text), region (text)
- fleet_capacity_kwh (numeric)
- captured_savings (numeric): realised customer saving to date, from their strategy
- theoretical_optimal (numeric): perfect-hindsight optimal over the same slots, from the oracle
- pct_of_optimal (numeric): captured_savings / theoretical_optimal, the skill metric
- rank (int): dense rank on pct_of_optimal
- updated_at (timestamptz)

`mart_company_pnl` — grain: one row, plus optional per-sim-day rows.
- sim_day (date, nullable for the total row)
- revenue_share (numeric), grid_services (numeric), costs (numeric)
- net (numeric): revenue_share + grid_services - costs
- customer_count (int)
- updated_at (timestamptz)

`mart_fleet` — grain: one total row plus per-region rows.
- region (text, null for total)
- total_capacity_kwh (numeric)
- flexible_kw (numeric): dispatchable power right now
- available_shift_kwh (numeric): energy we can move across the next window
- customer_count (int)
- updated_at (timestamptz)

`mart_support` — grain: one row.
- open_tickets (int), escalated (int)
- avg_response_secs (numeric), oldest_open_age_secs (numeric)
- updated_at (timestamptz)

---

## 5. The swarm

Five agents. Each is a soul file in `agents/souls/`, scoped blank-slate to its own tool allowlist, run on a cadence, coordinating only through shared Supabase state and the `tasks` queue. Every agent checks `kill_switch` at the top of every run, takes consequential state-changing actions, and logs to `decisions_log`.

| Agent | Cadence | Reads | Writes | Gated actions |
|---|---|---|---|---|
| CEO / Strategy | ~3 min real | all marts, tasks, decisions_log | tasks, decisions_log, pending_approvals | licensing moves, anything with spend |
| Trading / Ops | ~60s real (policy runs per sim slot) | tariff_prices, batteries, households, customers | trades, batteries (soc), decisions_log | customer strategy or pricing change |
| Growth / Marketing | ~2 to 3 min real | marts, customers, connections | customers, households, connections, strategy_versions, community_posts, decisions_log | any spend, any external send |
| Support / Community | event on new ticket or connection error + sweep ~2 min | support_tickets, customers, connections, marts | support_tickets, connections, community_posts, decisions_log | external send, refunds or goodwill credit |
| Finance / Compliance | ~2 min real | trades, ledger, marts | ledger, decisions_log, kill_switch (trip only) | none (it enforces, it does not spend) |

The strategy executor in `energy/policy.py` runs each customer's `strategy_preset` per slot and enforces `cycle_cap_per_day` and `reserve_soc_pct` as hard limits. The benchmark oracle in `energy/optimizer.py` computes `theoretical_optimal` with perfect hindsight under the same caps. The trading agent supervises strategy and exceptions on its slower cadence. See section 10 for the customer game.

---

## 6. Approval gate and guardrails

### Gate flow
1. An agent decides a high-stakes action and writes a row to `pending_approvals` with `status = 'pending'` and the action in `payload`.
2. `gate.py` sends a Telegram message with approve and reject buttons, recording `telegram_msg_id`.
3. The human taps. A Modal webhook updates `status` to `approved` or `rejected` and sets `resolved_by`, `resolved_at`.
4. The requesting agent polls its pending row and proceeds only on `approved`. On `rejected` it abandons the action and logs the outcome.

### Gated action types
`spend`, `pricing_change`, `external_send`, `licensing`. Anything touching real money, real external parties, customer pricing, or the licensing path is gated. Internal sim dispatch is not gated.

### Hard guardrails (enforced in code and tested in dbt)
- Cycle cap: no battery exceeds `cycle_cap_per_day`. Enforced in both the policy executor and the oracle, tested in `schema.yml`.
- Spend cap: cumulative simulated spend stays under the ceiling (see parameters). All spend is also individually gated.
- Regulatory line: no agent acts as if zapflex holds a VLP or supply licence. The licensing path is a gated decision only.
- Audit: every agent action lands in `decisions_log`. `kill_switch.halted = true` stops all agents at the top of their loop.
- Isolation: each agent is scoped to its own tables and tools per the allowlist above.

---

## 7. Parameters and defaults

Starting values, tune as needed. Hold these in env or a small `config` so they are not scattered.

- Revenue share: 0.20 of customer savings.
- Grid-services rate (simulated): a flexibility payment proportional to enrolled flexible capacity, default GBP 100 per kW per sim year, accrued per sim day. Tune for a legible demo number.
- Cycle cap: 1.5 cycles per battery per sim day.
- Spend ceiling: GBP 100 simulated, cumulative, across growth and ops. Every individual spend is gated regardless.
- Battery defaults: capacity 5 to 13.5 kWh, max charge and discharge 3 to 5 kW, round-trip efficiency 0.90, reserve SOC 0.10.
- Region: London (`C`) by default for the seeded fleet.
- Sim clock: 1 real second per simulated half-hour slot.
- Default strategy floor: around 85% of optimal, so the game is about beating the default.
- Models: configurable via OpenRouter. Default to a cost-efficient reasoning model for the high-frequency agents and a stronger model for the CEO. Minimum 64k context.

---

## 8. Build phases

Phase maps to the `tasks.phase` field and to the demo timeline.

### Prep (pre-built domain core, the two days before)
Octopus Agile ingestion (import and Agile Outgoing, London) into DuckDB and the Supabase `tariff_prices` table, the battery model, the strategy executor and the benchmark oracle, the household generator, the sim clock, the offline DuckDB backtest (perfect-hindsight optimal versus naive baseline), the Supabase operational schema, the dbt marts and tests. Arrive with these running. The domain core is a black box the agents call.

### Day-of (the 3-hour window)
The swarm, the harness (`runner.py`, `tools.py`, `gate.py`), the Telegram gate, the live dashboard, and the Modal schedules. Build in priority order:
- MVP that tells the whole story: CEO, Trading, Finance, plus the gate and the dashboard.
- Enrichment: Growth and Support.

If the clock bites, three agents with real autonomy and a clean oversight layer beat five half-built ones.

---

## 9. Non-goals

- No real VLP or wholesale-market integration, and no live grid APIs. Prices are replayed real Agile data.
- No real per-provider battery integration (the connector layer). Onboarding, connection, and telemetry are simulated via `connections`, `households`, and `energy/household.py`.
- Revenue is simulated and comes from the aggregation and grid-services slice, never a fantasy subscription.
- The dashboard is display-only.
- No agent framework. The database and the task queue are the orchestration.
- Keep scope to one clean autonomous loop with visible oversight.

---

## 10. Customer game and onboarding

### The game mechanic
The leaderboard only has a spread if dispatch follows each customer's own strategy. The engine is split in two:
- `energy/policy.py`, the strategy executor. Given a customer's `strategy_preset`, the price forecast, household load and solar, battery state, and the hard caps, it picks this slot's action. It drives each customer's `trades`, and its quality varies with the params. This is the customer's tunable trading agent.
- `energy/optimizer.py`, the benchmark oracle. Given the same inputs with perfect hindsight over the known 48 prices and the same hard caps, it computes the best achievable saving, which is `theoretical_optimal`.

The leaderboard ranks `pct_of_optimal = captured_savings / theoretical_optimal`, a hardware-normalised skill score. The zapflex default strategy is a strong floor (around 85% of optimal, echoing Predbat's default getting most of the way). The game is whether a customer's tuning beats the default. The hard caps apply in both engines, so nobody tunes their way into over-cycling and the safety envelope holds regardless of how aggressive a strategy is.

### Strategy params (stored in `batteries.strategy_preset`)
```json
{
  "charge_cheapest_slots": 12,
  "discharge_dearest_slots": 10,
  "export_threshold_p": 25,
  "cost_per_cycle_p": 5,
  "reserve_soc_pct": 0.10,
  "forecast_horizon_slots": 48
}
```
Skill comes from percentile-aware slot selection, respecting round-trip efficiency, exporting at peaks under Agile Outgoing, and cycling neither too much nor too little. Naive params leave money on the table.

### The real-customer flow (simulated here)
In production a customer buys a battery from a provider, signs up to zapflex, and authorises us to their battery, which means an OAuth connection to the provider cloud API or a local route through Home Assistant. They connect their Octopus account and region, then pick or tune a strategy. From then on zapflex reads their telemetry (state of charge, consumption, solar) and sends dispatch commands. The per-provider connector layer is the unglamorous slog that becomes a moat once built, and it is out of scope for the hackathon. We simulate it with the `connections`, `households`, and `strategy_versions` tables and `energy/household.py`, which generates per-slot load and optional solar from the household params so we store parameters rather than bulky time series.

### How the sim drives the game during the hands-off window
- Growth owns onboarding. It creates a customer, a household, a `connections` row (pending, then connected, with a small chance of error), and an initial strategy of varying quality. Most land on the default, a few tuned better or worse, giving the leaderboard a cluster plus a tail.
- A subset of customers tune their strategy over time. Growth nudges some `strategy_preset` values, which moves ranks and gives the community something to react to. Each change writes a `strategy_versions` row.
- Support handles `connections.status = 'error'`, troubleshooting to connected or escalating through the gate.
- Growth and Support post leaderboard updates and tips into `community_posts` and field "how do I climb" tickets. That is the social surface, and it makes the game visible on the dashboard.

---

## 11. Demo plan

The hands-off window is the demo. Once the schedules go live, the founders walk away. Sim-customers join, the policy executor trades, the ledger climbs, tickets get answered, the leaderboard moves, and the Telegram gate buzzes for the occasional high-stakes approval.

The dashboard shows one timeline with the prep-to-live handoff: the build tasks closed by humans and Claude Code over two days, then the operational tasks created and closed by the agents autonomously in the evening, colour-coded by actor. The headline is `mart_company_pnl.net`, the company made GBP X, with a full `decisions_log` audit trail and the human in the loop only for consequential calls.

Framing for the Elyos judge: the game assembles a flexibility fleet, optimisation is the hook, and the revenue is upstream in aggregation. That is the lesson their pivot taught, executed deliberately.
