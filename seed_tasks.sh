#!/usr/bin/env bash
./task.py add "Wire dbt-postgres to Supabase (session pooler, port 5432)" --category data
./task.py add "Build Octopus Agile ingestion: import + Agile Outgoing, London, into DuckDB and seed tariff_prices" --category energy_api
./task.py add "Build battery model and accelerated sim clock (replays real Agile slots)" --category harness
./task.py add "Build household generator: per-slot load and optional solar" --category data
./task.py add "Build strategy executor (energy/policy.py): runs strategy_preset per slot under hard caps" --category harness
./task.py add "Build benchmark oracle (energy/optimizer.py): perfect-hindsight optimum under same caps" --category data
./task.py add "Offline DuckDB backtest: default strategy vs oracle vs naive baseline" --category data
./task.py add "Create schema/002_operational.sql and apply the operational schema to Supabase" --category data
./task.py add "Build dbt marts (leaderboard, company_pnl, fleet, support) and schema.yml tests" --category data
./task.py add "Build Modal agent-runner skeleton (runner.py, tools.py)" --category harness
./task.py add "Write 5 soul files (ceo, trading, growth, support, finance) scoped to allowlists" --category harness
./task.py add "Build Telegram approval gate (gate.py): pending_approvals to buttons to resolve" --category harness
./task.py add "Build live dashboard: marts and tasks, realtime or poll, actor-coloured timeline" --category harness
./task.py add "Wire Modal schedules: dbt heartbeat and agent cadences" --category harness
./task.py add "End-to-end smoke test: agent task to approval to trade to mart to dashboard" --category harness
./task.py add "Secure sponsor credits: Modal, Supabase, Manus" --category ops
./task.py add "Confirm waitlist spot and form team" --category ops
