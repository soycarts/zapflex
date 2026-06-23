# CLAUDE.md — zapflex

## Mission
zapflex is an autonomous home-battery flexibility company run by a swarm of AI agents, built for the Cursor Hands Off hackathon (build a business that runs itself, then step away while it runs).

We optimise home batteries against dynamic electricity tariffs, assemble the optimised fleet, and earn from grid-services flexibility. The optimisation engine is the product. The agent swarm runs the company around it.

Authoritative design lives in `docs/SPEC.md`. Read it before any non-trivial work. Decisions live there and nowhere else.

## What "good" means here (the event scoring)
Keep all six in mind when making build choices: technical execution, product thinking, agent autonomy, UX clarity, real-world applicability, safety and oversight design.

## Stack
- Python 3.11+
- DuckDB: offline backtest and heavy historical optimisation
- Supabase (Postgres): live operational state, realtime, Data API
- dbt (dbt-postgres): analytical marts built on top of the operational tables
- Modal: compute for agent schedules, webhooks, and the dbt heartbeat
- OpenRouter or the Anthropic API: runtime inference for the deployed agents
- Telegram: the human approval gate

Build-time help (Cursor or Claude Code) is separate from run-time agent inference. The deployed agents call an inference API directly and need their own key. They cannot use a Cursor or Claude subscription.

## Architecture (two layers, one heartbeat)
- Operational layer: Supabase tables written live by the agents, plus `tariff_prices` reference data seeded in prep. `docs/SPEC.md` section 4 holds the canonical table set and column contracts. dbt does not own these.
- Analytical layer: dbt marts on top, `mart_leaderboard`, `mart_company_pnl`, `mart_fleet`, `mart_support`. The dashboard and the agents read marts, not raw tables.
- Heartbeat: agents write state continuously. A scheduled Modal job reruns dbt every 30 to 60 seconds (or only the changed marts) to refresh the marts. Everything reads the refreshed marts.

The leaderboard ranks each customer by percent of the oracle's perfect-hindsight optimal that their own strategy captured, a hardware-normalised skill metric, computed once in dbt so every consumer reads the same numbers.

## The swarm
Five agents, each defined by a soul file in `agents/souls/`, scoped blank-slate to its own tool allowlist, run on a cadence, coordinating only through shared Supabase state and the `tasks` queue. Every agent must take consequential, state-changing actions and log them.
- CEO / Strategy (`ceo.md`): plans, sets priorities, writes tasks, proposes high-stakes moves to the gate.
- Trading / Ops (`trading.md`): supervises the per-customer strategy executor and the benchmark oracle, writes trades.
- Growth / Marketing (`growth.md`): acquires sim-customers, generates community content.
- Support / Community (`support.md`): answers tickets and community questions, escalates the hard ones.
- Finance / Compliance (`finance.md`): tracks revenue and P&L, enforces spend and cycle caps.

## The harness
The swarm is four files plus the soul files plus shared Supabase state. No agent framework. The database and the task queue are the orchestration.
- `agents/runner.py`: the loop. Load a soul file and the agent's state, call the model, execute tool calls, log to `decisions_log`.
- `agents/tools.py`: the narrow tool registry. Each agent is scoped to its own allowlist.
- `agents/gate.py`: the Telegram approval gate.
- `app/modal_app.py`: schedules, webhooks, and the dbt heartbeat.

## Conventions
- Repo layout is fixed. See `docs/SPEC.md` for the full tree. Agent instruction files go in `agents/souls/`. Canonical SQL in `schema/`. dbt models in `dbt/models/`. The pre-built domain core in `energy/`.
- One source of truth per thing: decisions in `docs/SPEC.md`, tasks in Supabase, code in the repo. Update the source, and never restate it elsewhere.
- Secrets live in `.env` only, never in code or commits. See `.env.example` for the variable names.
- Document each module with a one-line docstring. Skip per-script READMEs.

## Task discipline (canonical: the Supabase `tasks` table via `./task.py`)
- At the start of work: `./task.py list --status todo`.
- Before starting a task: `./task.py start <id>`.
- On completion: `./task.py done <id> --by claude_code --note "<what changed>" --ref "<commit or PR>"`.
- Create any task you discover: `./task.py add "<title>" --category <area>`.
- Do not track tasks in local .md files. This table is canonical, and the agents write to it too.

## Definition of done (this is what prevents drift)
A task is done only when its check passes. Never mark a task done with a red test.
- Data invariants are dbt tests in `dbt/models/schema.yml`: the ledger reconciles, capacity never goes negative, no battery exceeds its cycle cap.
- The domain core and harness have pytest coverage in `tests/`.
- Run the relevant tests before calling `./task.py done`.

## Hard guardrails (non-negotiable)
- Protect customer assets above short-term gain. Never let optimisation over-cycle a battery beyond its cycle cap.
- Respect spend caps. Any spend, pricing change, external send, or move toward licensing goes to the human gate: write to `pending_approvals` and wait for Telegram approval.
- Stay inside the regulatory line. Behind-the-meter optimisation is unlicensed. Wholesale or Balancing Mechanism participation needs a VLP. Never act as if we hold a licence we do not have.
- Every agent action is logged to `decisions_log`. A `kill_switch` flag halts all agents. Each agent is scoped to its own tables and tools.

## Non-goals (do not build these)
- No real VLP or wholesale-market integration, and no live grid APIs. Prices are replayed real Agile data.
- No real per-provider battery integration. Onboarding and telemetry are simulated.
- Revenue is simulated, and comes from the aggregation and grid-services slice, never a fantasy subscription.
- The dashboard is display-only.
- Keep scope to one clean autonomous loop with visible oversight. Three agents working with real autonomy beat five half-built ones.
