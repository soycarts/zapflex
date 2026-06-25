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
      sources.yml           # raw operational tables declared as sources
      staging/
      marts/                # mart_leaderboard, mart_company_pnl, mart_fleet, mart_support
      schema.yml            # dbt tests = data contracts + guardrails
  energy/                   # pre-built domain core (prep days)
    ingest_agile.py         # Octopus Agile import + Agile Outgoing -> DuckDB + tariff_prices
    battery.py              # battery model
    policy.py               # strategy executor: runs each customer's strategy per slot
    optimizer.py            # benchmark oracle: perfect-hindsight optimum
    household.py            # per-slot load and solar: a forecast for the policy, an actual for settlement
    sim_clock.py            # accelerated simulation clock (replays real Agile slots)
    backtest.py             # offline DuckDB backtest: baseline vs optimal
  agents/                   # the swarm
    runner.py               # the thin agent-runner (harness core)
    tools.py                # tool registry: DB reads/writes, the gate call, optional PayPal payment
    gate.py                 # Telegram approval gate
    souls/                  # agent instruction files
      ceo.md
      trading.md
      growth.md
      support.md
      finance.md
  app/
    modal_app.py            # Modal: schedules, webhooks, the dbt heartbeat
  dashboard/                # live dashboard + judge game UI (Vercel; reads marts, tasks)
  web/                      # optional marketing landing page (zapflex.ai)
  tests/                    # pytest for the domain core and the harness
```

---

## 3. Architecture

Two layers, one heartbeat.

- Operational layer: Supabase (Postgres) tables, written live by the agents. The source of truth.
- Analytical layer: dbt marts built on top of the operational tables. The dashboard and the agents read marts, never raw tables, for any derived figure.
- Heartbeat: agents write state continuously. A scheduled Modal job reruns dbt every 30 to 60 seconds (or only the changed marts, invoked through the dbtRunner Python API to skip CLI cold starts) to refresh the marts. Everything reads the refreshed marts.

Data flow per cycle: the policy executor dispatches each battery for the current sim slot per the customer's strategy and writes `trades`; the oracle scores the achievable optimum for the leaderboard; finance books revenue into `ledger`; growth and support mutate `customers`, `connections`, `community_posts`, `support_tickets`; dbt rebuilds the marts; CEO reads the marts and writes `tasks`; high-stakes actions queue to `pending_approvals` and wait on Telegram.

### Tooling and responsibilities

Three tools, distinct jobs.
- DuckDB: prep only, on your machine. Pulls the raw Agile history and runs the offline backtest (perfect-hindsight optimal versus naive baseline across months of prices) to validate the policy and oracle economics. It is not in the runtime path.
- Supabase (Postgres): the live store. Holds the operational state the sim and swarm write to, the reference prices in `tariff_prices`, and serves the dashboard. The sim clock, policy executor, and oracle write here in prep and on the day alike, so sim rows land in Supabase you can watch live.
- dbt: the transformation layer on top of Supabase. Turns the operational tables into marts on the heartbeat. It stores nothing of its own.

The live data path is sim clock to Supabase to dbt to marts to dashboard, with DuckDB beside it for offline validation. Reference prices live in both: DuckDB for the backtest, Supabase for the live replay, written by the same ingestion.

### Tariff data

The data is the easy part. Octopus publishes the Agile prices through a public REST API that needs no account and no auth key.

- The products endpoint returns every half-hourly unit rate for a regional tariff code over a time range.
- Two directions on the same API: import from the Agile tariff, export from Agile Outgoing. Two-way price exposure for free.
- Region: London is the `C` regional code (the `-C` tariff-code variant).
- Source cadence: the 48 half-hourly rates for the next day publish around 4pm, set by the EPEX day-ahead auction.
- Each record is a 30-minute bucket with `valid_from`, `valid_to`, and `value_inc_vat` in pence. History is paginated via `period_from` and `period_to`, following the `next` links.
- Do not hard-code a product version, they are versioned and change. In `ingest_agile.py` (prep), hit `/v1/products/`, find the current Agile product, build the import code as `E-1R-{PRODUCT}-C` and the export code from the Agile Outgoing product, both region `C`.
- Pull about six months. At 1 real second per slot the hands-off window replays roughly 180 sim days, so six months gives one clean forward pass with non-repeating data and more for the backtest. Land it in DuckDB for the backtest and seed `tariff_prices` for the live replay.

Units: tariff prices are pence per kWh (import inc VAT). Money fields (`trades.cashflow`, `ledger.amount`) are GBP. Convert pence to GBP by dividing by 100.

### Simulation clock

During the hands-off window the sim runs fast so the agents live through many days and the dashboard visibly ticks.

- Default: 1 real second = 1 simulated half-hour slot. A sim day (48 slots) is 48 real seconds. A 2.5-hour hands-off window covers roughly 180 sim days. Configurable in `sim_clock.py`.
- The sim replays the ingested real Agile slots in order at the configured speed. `sim_time` on operational rows is the `slot_start` of the slot being replayed, so the simulated days are real historical pricing days, separate from `created_at` (wall clock).
- The deterministic policy executor runs every sim slot and is cheap. The trading agent (LLM) supervises on a slower real-time cadence and does not make a per-slot LLM call. This keeps inference cost and latency sane.
- Information: prices are fully visible, the whole 48-slot day-ahead curve, exactly as Octopus publishes at 4pm. The genuine uncertainty is the household. The policy sees a forecast of load and solar; settlement and the oracle use the actual realised series. The gap between forecast and actual is where skill lives. See section 10.
- Display offset: to make the dashboard look current, add a fixed offset of N days at render time only. The stored `slot_start` and `sim_time` keep the real timestamps, which avoids timestamp bugs and keeps the data honest.

### dbt setup

Standard layout against Supabase.
- `models/sources.yml`: declares the raw operational tables over the `public` schema.
- `models/staging/`: thin `stg_` models that clean and rename. Kept light.
- `models/marts/`: `mart_leaderboard`, `mart_company_pnl`, `mart_fleet`, `mart_support`, materialised as tables so the dashboard can poll or subscribe.
- `models/schema.yml`: the tests that double as guardrails (ledger reconciles, capacity never negative, no battery over its cycle cap, `pct_of_optimal` within 0 to 1).
- `profiles.yml`: dbt-postgres on the Supabase session pooler, port 5432.
- Heartbeat: a Modal function runs `dbt run --select marts` through the dbtRunner API every 30 to 60 seconds. In prep you run `dbt run` locally against Supabase.

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
  strategy_preset     jsonb,                          -- household forecast model + knowledge knobs (see section 10)
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
  has_ev             boolean not null default false,
  occupancy_profile  text not null default 'standard',  -- shapes the load curve
  load_volatility    numeric not null default 0.15,      -- forecast-error magnitude; higher = harder to predict
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
  action           text not null,                     -- charge|discharge|idle, the planned action
  energy_kwh       numeric not null default 0,        -- actual energy moved after settlement
  price_p_per_kwh  numeric not null,                  -- pence/kWh (import or export) at that slot
  cashflow         numeric not null,                  -- signed GBP, settled against actual load and solar
  cycles_used      numeric not null default 0,        -- fractional cycles this action added
  created_at       timestamptz not null default now()
);

create table ledger (
  id               bigint generated always as identity primary key,
  customer_id      bigint references customers(id),   -- null for company-wide entries
  sim_time         timestamptz not null,
  entry_type       text not null,                     -- revenue_share|grid_services|cost
  amount           numeric not null,                  -- signed GBP, company money
  payment_ref      text,                              -- PayPal sandbox transaction id, null if simulated only
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

-- Oracle output: the perfect-hindsight optimum per customer, read by mart_leaderboard.
create table benchmarks (
  id              bigint generated always as identity primary key,
  customer_id     bigint not null references customers(id),
  window_start    timestamptz not null,
  window_end      timestamptz not null,
  optimal_savings numeric not null,                 -- GBP, best achievable over the window
  updated_at      timestamptz not null default now()
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
- captured_savings (numeric): customer saving to date, summed from `trades`
- theoretical_optimal (numeric): perfect-hindsight optimal, summed from `benchmarks` (written by the oracle)
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

The strategy executor in `energy/policy.py` plans each customer's day-ahead dispatch on the household forecast built from their `strategy_preset` (forecast model), settles it against the actual, and enforces `cycle_cap_per_day` and `reserve_soc_pct` as hard limits. The benchmark oracle in `energy/optimizer.py` computes each household's perfect-hindsight optimum against its actual realised load and solar under the same caps, and writes it to `benchmarks`, which `mart_leaderboard` divides into captured savings. The trading agent supervises strategy and exceptions on its slower cadence. See section 10 for the customer game.

---

## 6. Approval gate and guardrails

### Gate flow
1. An agent decides a high-stakes action and writes a row to `pending_approvals` with `status = 'pending'` and the action in `payload`.
2. `gate.py` sends a Telegram message with approve and reject buttons, recording `telegram_msg_id`.
3. The human taps. A Modal webhook updates `status` to `approved` or `rejected` and sets `resolved_by`, `resolved_at`.
4. The requesting agent polls its pending row and proceeds only on `approved`. On `rejected` it abandons the action and logs the outcome.

Tools are plain Python functions in `agents/tools.py`, exposed to the model through the API function-calling interface. `runner.py` loops: the model returns tool calls, the runner executes the matching function and feeds the result back, repeating until the model stops. The outbound gate is one such tool, `request_approval(action_type, payload)`. The Telegram mechanics (inline keyboard and callback handling) are ported from Hermes; the Supabase and Modal wiring around them is new.

### Gated action types
`spend`, `pricing_change`, `external_send`, `licensing`. Anything touching real money, real external parties, customer pricing, or the licensing path is gated. Internal sim dispatch is not gated.

### Payments (optional, PayPal sandbox)
The simulated `ledger` is the source of truth for revenue, and the demo works on it alone. If the PayPal agent sandbox is available, wire it as an optional enhancement: the Finance agent books the `ledger` entry as usual, and a sandbox payment fires alongside via a `paypal_payment` tool in `tools.py`, with the transaction id stored in `ledger.payment_ref`. Payments are gated like any spend (`action_type = spend`) and route through `pending_approvals`, so a payout passes through your approval and becomes a live demonstration of the human gate. If the sandbox is unavailable or misbehaves, drop the tool and the simulated ledger carries the demo unchanged.

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
- Naive-forecast floor: around 85% of a solar-and-EV household's optimal (higher for predictable homes), the gap being forecast-error cost. Learning the household's routine — a sharper forecast model — climbs above it, toward ~97%.
- Forecast noise: per-household `load_volatility`, plus the unlearnable part of the EV/solar pattern (skipped days, weather scatter), sets how hard a household is to predict and holds the ceiling below 100%. The learnable routine (EV schedule, solar climatology) is what forecast skill captures. Tune both for a clear, sensible ordering.
- Models: configurable via OpenRouter. Default to a cost-efficient reasoning model for the high-frequency agents and a stronger model for the CEO. Minimum 64k context.

---

## 8. Build phases

Phase maps to the `tasks.phase` field and to the demo timeline.

### Prep (pre-built domain core, the two days before)
Octopus Agile ingestion (import and Agile Outgoing, London) into DuckDB and the Supabase `tariff_prices` table, the battery model, the strategy executor and the benchmark oracle, the household generator, the sim clock, the offline DuckDB backtest (perfect-hindsight optimal versus naive baseline), the Supabase operational schema, the dbt marts and tests. Also pre-build the front-end surfaces against the seeded marts: the dashboard shell, the judge game UI (section 11), and optionally the marketing page, deployed to Vercel. Arrive with all of this running. The domain core and the surfaces are scaffolding the event only wires to live data. The key prep checkpoint is the end-to-end data feed test: ingest real Agile, seed `tariff_prices`, seed two or three customers with different households and forecast models, run the sim clock so the policy writes `trades` and the oracle writes `benchmarks` into Supabase, run dbt, and confirm a real spread in `mart_leaderboard.pct_of_optimal`. You should see the sim rows landing in Supabase live.

### Day-of (the 3-hour window)
The swarm, the harness (`runner.py`, `tools.py`, `gate.py`), the Telegram gate, wiring the pre-built dashboard and judge UI to live data, and the Modal schedules. Build in priority order:
- MVP that tells the whole story: CEO, Trading, Finance, plus the gate and the dashboard.
- Enrichment: Growth and Support.

If the clock bites, three agents with real autonomy and a clean oversight layer beat five half-built ones.

---

## 9. Non-goals

- No real VLP or wholesale-market integration, and no live grid APIs. Prices are replayed real Agile data.
- No real per-provider battery integration (the connector layer). Onboarding, connection, and telemetry are simulated via `connections`, `households`, and `energy/household.py`.
- Revenue follows the aggregation and grid-services model, never a fantasy subscription. It is simulated in the ledger, optionally mirrored as PayPal sandbox payments.
- The dashboard is display-only.
- No autonomous coding agent. Code is build-time work by the team and Claude Code; the run-time swarm is scoped to running the business.
- No agent framework. The database and the task queue are the orchestration.
- Keep scope to one clean autonomous loop with visible oversight.

---

## 10. Customer game and onboarding

### The game mechanic
Start from what is known. Octopus publishes all 48 half-hourly Agile prices for the next day at 4pm, so every strategy plans against the full day-ahead price curve, exactly as in production. With prices known, single-battery arbitrage is nearly solved: given the actual household, the optimal plan lands within a percent or two of the oracle. The headroom is elsewhere.

The real headroom is forecasting the household. Consumption and solar tomorrow are not known. They follow routine, weather, and the odd surprise (an EV plugging in, a dull solar day). So the model splits forecast from actual:
- `energy/household.py` produces the actual realised load and solar — a learnable routine (EV schedule, solar climatology) plus unlearnable noise. `energy/forecast.py` produces the forecast the policy plans on, sharp or naive depending on how much of the routine the model has learned.
- `energy/policy.py`, the strategy executor, plans the day-ahead dispatch (a linear program) on the forecast and the known prices, under the hard caps. The customer's tunable trading agent *is* the forecast model it plans against.
- Settlement runs the chosen action against the actual load and solar to get the true grid import and export, and so the real `trades` cashflow. The battery acts on the plan; the cashflow reflects what happened.
- `energy/optimizer.py`, the benchmark oracle, optimises against the actual with perfect hindsight, per household, and writes the per-household optimum to `benchmarks`.

No forecast can reach ~100% by construction: a household's routine is learnable, but its noise — a skipped EV day, a dull solar afternoon, an unplanned daytime charge — is not. The gap is honest forecast-error cost, and closing it is the game. The leaderboard ranks `pct_of_optimal = captured_savings / optimal` per customer, averaged across the sim days so skill dominates the noise. The hard caps apply in both the policy and the oracle, so nobody trades their way into over-cycling and the safety envelope holds.

### The lever: the household forecast model (stored in `batteries.strategy_preset`)
Prices are fully visible, so there is no price-side knob — and risk knobs do not earn their keep. With the full day-ahead curve, the forecast-optimal plan is the certainty-equivalent optimum, so deviating from it with a reserve buffer or an export floor only ever costs yield. Both were built and swept, and both are anti-knobs; they are not exposed. The one lever that moves `pct_of_optimal` is the **household forecast** the policy plans against — specifically how much of the home's routine the model has learned.

`energy/household.py` splits each home into a *learnable* routine (a consistent daily EV charging window and baseline kWh; a solar climatology) and *unlearnable* noise (load noise, occasional skipped EV days, magnitude scatter, rare daytime top-ups). `energy/forecast.py` then builds the forecast at a knowledge level, with two continuous knobs in [0, 1] surfaced as named models:
```json
{
  "forecast_model": "learned",
  "solar_knowledge": 1.0,   // 0 = optimistic clear-sky, 1 = climatological mean
  "ev_knowledge": 1.0       // 0 = ignore the EV, 1 = anticipate the charging routine
}
```
- `naive` (0, 0): profile load and clear-sky solar, no EV. The knowledge-0 floor.
- `seasonal` (1, 0): de-rates solar to its climatological mean. Helps solar homes.
- `learned` (1, 1): also anticipates the EV charging routine. The ceiling for an EV home.

Skill is matching the model to the household, and it is two-sided: learning a routine the home has lifts it toward the oracle, but predicting a routine it does not have — a phantom EV — plans for load that never arrives and loses yield. The right model depends on the household. A stable no-solar home sits near the top on `naive` whatever it does; a solar-and-EV home has large headroom that only `learned` reaches. 30-day backtest: stable ~97% on `naive`, solar ~96% on `seasonal`, solar-and-EV ~86% on `naive` rising to ~97% on `learned`. The unlearnable noise holds the ceiling below 100%.

### The real-customer flow (simulated here)
In production a customer buys a battery from a provider, signs up to zapflex, and authorises us to their battery, which means an OAuth connection to the provider cloud API or a local route through Home Assistant. They connect their Octopus account and region, then pick or tune a strategy. From then on zapflex reads their telemetry (state of charge, consumption, solar) and sends dispatch commands. The per-provider connector layer is the unglamorous slog that becomes a moat once built, and it is out of scope for the hackathon. We simulate it with the `connections`, `households`, and `strategy_versions` tables and `energy/household.py`, which generates per-slot load and optional solar from the household params so we store parameters rather than bulky time series.

### How the sim drives the game during the hands-off window
- Growth owns onboarding. It creates a customer, a varied household (some with solar or an EV, some stable), a `connections` row (pending, then connected, with a small chance of error), and an initial forecast model. Most start on `naive` or `seasonal` — the knowledge floor, routine not yet learned — giving the leaderboard a cluster plus a tail of unlearned homes.
- The Trading agent learns each home's routine over time and raises its forecast model toward `learned` (for instance once it has watched a customer's EV charge on a steady evening schedule), which moves ranks and gives the community something to react to. Each change writes a `strategy_versions` row. This is the demonstrable autonomy win: a solar-and-EV customer seeded on `seasonal` at ~82% climbs toward ~99% once its routine is learned.
- Support handles `connections.status = 'error'`, troubleshooting to connected or escalating through the gate.
- Growth and Support post leaderboard updates and tips into `community_posts` and field "how do I climb" tickets. That is the social surface, and it makes the game visible on the dashboard.

### Stretch and vision (not built for the hackathon)
A second uncertainty axis exists and is worth recording, though it stays off the build path. Today the game's uncertainty is the household. The market is also uncertain: tomorrow's prices can run above or below the recent norm because of a heatwave, a still wind day, or a big match driving demand. A player who reads those conditions and positions for a price spike is doing real, skillful work the day-ahead curve does not reveal until 4pm.

Building it would mirror the household split: withhold the next day's prices from the policy, give it a noisy weather-and-demand signal to forecast against, and settle on the real prices when they arrive. The data exists, since NESO publishes national half-hourly demand. So the honest version forecasts prices directly. A separate region-demand input would largely duplicate the price the player already sees, because Agile rises when the country draws more. It is a second uncertainty engine on top of the household one, which is why it stays a stretch.

The data-network angle follows from it: aggregate the players' positioning and you get a crowd-sourced demand-and-price forecast, a signal that is itself saleable and a gesture at the flex network's data value. Worth a sentence in the demo, with no aggregation built.

---

## 11. Surfaces

Three web surfaces, all build-time work (the team plus Claude Code), all reading the same Supabase backend. None is written by a run-time agent. What runs hands-off is the content the agents write to the database, which these surfaces render.

### Live dashboard (priority 1)
The demo centrepiece. Reads the marts and the `tasks` board and shows the actor-coloured prep-to-live timeline, the leaderboard, the running P&L, the agent activity feed from `decisions_log`, and the pending approvals. Realtime or short polling.

### Judge game UI (priority 2, high value)
The interactive moment for the judges. A judge picks a forecast model for a household — `naive`, `seasonal`, `learned`, or the two knowledge sliders underneath — and watches their rank move on the leaderboard as the forecast gets sharper. This lands UX clarity and product thinking together, and it runs on the existing backend with no new agent:
- The judge's inputs write a judge-tagged sandbox `customers` and `batteries` row (the model in `strategy_preset`), flagged so the Growth agent and the sim fleet ignore it.
- The deterministic policy executor plans the dispatch on that forecast and settles it against the household's actual, exactly as for a real customer.
- The oracle gives `theoretical_optimal`, dbt computes `pct_of_optimal`, and the judge appears on the leaderboard.

Build it as a second view in the dashboard app, deployed and working before the event so judges use it live.

### Marketing page (priority 3, optional)
A landing page at zapflex.ai, or a Vercel subdomain, with the .ai domain as optional polish for VC optics. Lowest priority, since the dashboard matters more than a marketing page for the demo. If built, it reads `community_posts` and the leaderboard, so it looks alive because the agents are writing the database it renders.

---

## 12. Demo plan

The hands-off window is the demo. Once the schedules go live, the founders walk away. Sim-customers join, the policy executor trades, the ledger climbs, tickets get answered, the leaderboard moves, and the Telegram gate buzzes for the occasional high-stakes approval.

The dashboard shows one timeline with the prep-to-live handoff: the build tasks closed by humans and Claude Code over two days, then the operational tasks created and closed by the agents autonomously in the evening, colour-coded by actor. The headline is `mart_company_pnl.net`, the company made GBP X, with a full `decisions_log` audit trail and the human in the loop only for consequential calls.

During the live demo, judges can open the game UI, tune a strategy, and watch their own entry climb the leaderboard, which lets them feel the autonomy firsthand.

Framing for the Elyos judge: the game assembles a flexibility fleet, optimisation is the hook, and the revenue is upstream in aggregation. That is the lesson their pivot taught, executed deliberately.