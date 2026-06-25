-- 002_operational.sql
-- Operational tables for zapflex. Apply once via psql or the Supabase SQL editor.
-- tariff_prices already exists (created by energy/ingest_agile.py); do not recreate it.
-- Canonical field reference: docs/SPEC.md section 4.

create table if not exists customers (
  id                  bigint generated always as identity primary key,
  handle              text not null,
  region              text not null,
  import_tariff       text not null default 'AGILE',
  export_tariff       text,
  status              text not null default 'active',
  acquisition_source  text,
  revenue_share_pct   numeric not null default 0.20,
  sim_joined_at       timestamptz,
  created_at          timestamptz not null default now()
);

create table if not exists batteries (
  id                  bigint generated always as identity primary key,
  customer_id         bigint not null references customers(id),
  brand               text,
  capacity_kwh        numeric not null,
  max_charge_kw       numeric not null,
  max_discharge_kw    numeric not null,
  round_trip_eff      numeric not null default 0.90,
  reserve_soc_pct     numeric not null default 0.10,
  cycle_cap_per_day   numeric not null default 1.5,
  current_soc_kwh     numeric not null default 0,
  strategy_preset     jsonb,
  created_at          timestamptz not null default now()
);

create table if not exists connections (
  id                  bigint generated always as identity primary key,
  customer_id         bigint not null references customers(id),
  battery_id          bigint references batteries(id),
  provider            text not null,
  status              text not null default 'pending',
  error_reason        text,
  connected_at        timestamptz,
  last_telemetry_at   timestamptz,
  created_at          timestamptz not null default now()
);

create table if not exists households (
  customer_id         bigint primary key references customers(id),
  annual_kwh          numeric not null default 3500,
  has_solar           boolean not null default false,
  solar_kwp           numeric not null default 0,
  has_ev              boolean not null default false,
  occupancy_profile   text not null default 'standard',
  load_volatility     numeric not null default 0.15,
  created_at          timestamptz not null default now()
);

create table if not exists strategy_versions (
  id                  bigint generated always as identity primary key,
  battery_id          bigint not null references batteries(id),
  version             int not null,
  params              jsonb not null,
  author_type         text not null,
  sim_time            timestamptz,
  created_at          timestamptz not null default now()
);

create table if not exists trades (
  id                  bigint generated always as identity primary key,
  battery_id          bigint not null references batteries(id),
  customer_id         bigint not null references customers(id),
  sim_time            timestamptz not null,
  action              text not null,
  energy_kwh          numeric not null default 0,
  price_p_per_kwh     numeric not null,
  cashflow            numeric not null,
  cycles_used         numeric not null default 0,
  created_at          timestamptz not null default now()
);

create table if not exists ledger (
  id                  bigint generated always as identity primary key,
  customer_id         bigint references customers(id),
  sim_time            timestamptz not null,
  entry_type          text not null,
  amount              numeric not null,
  note                text,
  created_at          timestamptz not null default now()
);

create table if not exists support_tickets (
  id                  bigint generated always as identity primary key,
  customer_id         bigint references customers(id),
  channel             text not null default 'discord',
  subject             text,
  body                text not null,
  status              text not null default 'open',
  priority            int not null default 3,
  answered_by         text,
  resolution          text,
  created_at          timestamptz not null default now(),
  answered_at         timestamptz
);

create table if not exists community_posts (
  id                  bigint generated always as identity primary key,
  customer_id         bigint references customers(id),
  author_type         text not null,
  author_name         text,
  channel             text not null default 'discord',
  body                text not null,
  created_at          timestamptz not null default now()
);

create table if not exists pending_approvals (
  id                  bigint generated always as identity primary key,
  requested_by        text not null,
  action_type         text not null,
  payload             jsonb not null,
  status              text not null default 'pending',
  telegram_msg_id     text,
  resolved_by         text,
  created_at          timestamptz not null default now(),
  resolved_at         timestamptz
);

create table if not exists decisions_log (
  id                  bigint generated always as identity primary key,
  agent               text not null,
  cycle               int,
  sim_time            timestamptz,
  action              text not null,
  rationale           text,
  state_summary       jsonb,
  tasks_created       jsonb,
  approvals_requested jsonb,
  created_at          timestamptz not null default now()
);

create table if not exists kill_switch (
  id          int primary key default 1,
  halted      boolean not null default false,
  reason      text,
  updated_at  timestamptz not null default now(),
  constraint kill_switch_single_row check (id = 1)
);

-- Seed the kill_switch row if not present.
insert into kill_switch (id, halted) values (1, false)
  on conflict (id) do nothing;

-- benchmarks: oracle's perfect-hindsight result per customer per sim day,
-- used by dbt to compute theoretical_optimal for the leaderboard.
create table if not exists benchmarks (
  id              bigint generated always as identity primary key,
  customer_id     bigint not null references customers(id),
  window_start    timestamptz not null,
  window_end      timestamptz not null,
  optimal_savings numeric not null,
  updated_at      timestamptz not null default now(),
  unique (customer_id, window_start)
);

-- Indexes for hot query paths.
create index if not exists trades_customer_sim_time_idx    on trades (customer_id, sim_time);
create index if not exists trades_battery_sim_time_idx     on trades (battery_id, sim_time);
create index if not exists ledger_customer_sim_time_idx    on ledger (customer_id, sim_time);
create index if not exists benchmarks_customer_window_idx  on benchmarks (customer_id, window_start);
create index if not exists decisions_log_agent_created_idx on decisions_log (agent, created_at);

-- Enable realtime on tables the dashboard and agents watch.
alter publication supabase_realtime add table trades;
alter publication supabase_realtime add table benchmarks;
alter publication supabase_realtime add table decisions_log;
alter publication supabase_realtime add table pending_approvals;
alter publication supabase_realtime add table kill_switch;
