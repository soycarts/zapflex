# zapflex

**An autonomous home-battery flexibility company, run by a swarm of AI agents.**

Built for the Cursor "Hands Off" hackathon — the challenge to build a business that runs itself, then step away while it runs. zapflex optimises home batteries against dynamic electricity tariffs, assembles the optimised fleet, and earns from grid-services flexibility. The optimisation engine is the product; the agent swarm runs the company around it.

- **Live dashboard:** https://zapflex.vercel.app/
- **The swarm runs autonomously** — once the schedules go live, the founders walk away. Sim-customers join, batteries trade, the ledger climbs, tickets get answered, the leaderboard moves, and the Telegram gate buzzes for the occasional high-stakes approval.

---

## What it does

zapflex creates value in two layers:

- **Product layer (unlicensed):** optimise home batteries behind the meter against dynamic Octopus **Agile** tariffs — Agile import and Agile Outgoing export. This is the saving that needs no licence.
- **Revenue layer (the real money):** aggregate the optimised fleet and earn **grid-services / flexibility** revenue. The optimiser is the hook; the money is upstream in aggregation.

The go-to-market wedge is a gamified "personal trader" and a **skill-normalised leaderboard** that ranks each customer by the *percentage of the oracle's perfect-hindsight optimum* their strategy captured (`pct_of_optimal`). Because prices are fully visible (Octopus publishes all 48 half-hourly rates for the next day at 4pm), the real skill is **forecasting the household** — its EV routine and solar climatology — not guessing prices. That hardware-normalised skill metric is computed once in dbt so every consumer reads the same numbers.

---

## The swarm

Five agents, each defined by a soul file in `agents/souls/`, each scoped blank-slate to its own tool and table allowlist, each running on its own cadence. They coordinate **only** through shared Supabase state and a `tasks` queue. There is **no agent framework** — the database and the task queue *are* the orchestration.

| Agent | Role |
|---|---|
| **CEO / Strategy** (`ceo.md`) | Plans, sets priorities, writes tasks, proposes high-stakes moves to the gate. |
| **Trading / Ops** (`trading.md`) | Supervises the per-customer strategy executor and benchmark oracle; writes trades, learns household routines and lifts forecast models. |
| **Growth / Marketing** (`growth.md`) | Acquires sim-customers, onboards batteries, generates community content. |
| **Support / Community** (`support.md`) | Answers tickets and community questions, troubleshoots connection errors, escalates the hard ones. |
| **Finance / Compliance** (`finance.md`) | Tracks revenue and P&L, enforces spend and cycle caps, can trip the kill switch. |

---

## Architecture — two layers, one heartbeat

```
sim clock → Supabase (operational tables) → dbt marts → dashboard
                                  ↑                          ↑
                            the agents write          agents + judges read
```

- **Operational layer:** Supabase (Postgres) tables written live by the agents and the sim engine — the source of truth. `tariff_prices` reference data is seeded in prep from real Agile history.
- **Analytical layer:** dbt marts built on top — `mart_leaderboard`, `mart_company_pnl`, `mart_fleet`, `mart_support`. The dashboard and the agents read **marts, never raw tables**, for any derived figure.
- **Heartbeat:** agents write state continuously; a scheduled Modal job reruns dbt every 30–60 seconds (via the `dbtRunner` API to skip CLI cold starts) to refresh the marts. Everything reads the refreshed marts.

Supporting pieces:
- **DuckDB** runs the offline backtest in prep (perfect-hindsight optimal vs naive baseline across months of real prices). It is not in the runtime path.
- **OpenRouter / Anthropic** provide agent inference at runtime (a cost-efficient reasoning model for the high-frequency agents, a stronger one for the CEO). Self-hosted vLLM inference is also wired via Modal (`app/modal_inference.py`).
- **Telegram** is the human approval gate.

---

## Safety & oversight

Oversight is designed in, not bolted on:

- **Human approval gate (Telegram):** any spend, pricing change, external send, or move toward licensing is written to `pending_approvals` and waits for a Telegram approve/reject tap before the agent proceeds.
- **Kill switch:** a `kill_switch` flag halts every agent at the top of its loop.
- **Per-agent isolation:** each agent is scoped to its own tables and its own tool allowlist.
- **Full audit trail:** every agent action is logged to `decisions_log` with its rationale.
- **Hard guardrails (enforced in code, tested in dbt):** no battery exceeds its `cycle_cap_per_day`; cumulative simulated spend stays under the ceiling; no agent acts as if zapflex holds a VLP or supply licence — behind-the-meter optimisation is unlicensed, and the licensing path is a gated decision only.

---

## Tech stack

- **Python 3.11+** — domain core and agent harness
- **DuckDB** — offline backtest and heavy historical optimisation
- **Supabase (Postgres)** — live operational state, realtime, Data API
- **dbt (dbt-postgres)** — analytical marts on top of the operational tables
- **Modal** — compute for the dbt heartbeat, schedules, webhooks, and inference
- **OpenRouter / Anthropic** — runtime agent inference
- **Telegram** — the human approval gate
- **Vercel** — the live dashboard (Next.js)

---

## Repo layout — where to look

| Path | What's there |
|---|---|
| `docs/SPEC.md` | The canonical design doc. Read this first — every decision lives here. |
| `agents/souls/` | The five agent soul files (`ceo`, `trading`, `growth`, `support`, `finance`). |
| `agents/runner.py` | The thin agent loop: load a soul + state, call the model, execute tool calls, log. |
| `agents/tools.py` | The narrow per-agent tool registry (DB reads/writes, the gate call). |
| `agents/gate.py` | The Telegram approval gate. |
| `agents/sim.py` | The live sim engine — the heartbeat that moves the fleet each sim day. |
| `energy/` | Pre-built domain core: `ingest_agile`, `battery`, `policy` (strategy executor), `optimizer` (benchmark oracle), `household`, `forecast`, `sim_clock`, `backtest`. |
| `schema/` | Canonical Supabase DDL (`001_tasks.sql`, `002_operational.sql`). |
| `dbt/models/` | Sources, staging, marts, and the tests that double as data contracts. |
| `app/modal_inference.py` | Modal-hosted inference for the deployed agents. |
| `dashboard/` | The live dashboard + judge game UI (Next.js on Vercel). |
| `task.py` | The task CLI over the canonical Supabase `tasks` table. |

---

## Try it as a judge

Open the [dashboard](https://zapflex.vercel.app/), pick a forecast model for a household — `naive`, `seasonal`, or `learned` (or the knowledge sliders underneath) — and watch your entry climb the leaderboard as the forecast sharpens. It runs on the same backend the swarm uses, with no new agent: the deterministic policy plans the dispatch, the oracle scores the optimum, and dbt computes your `pct_of_optimal` live.
