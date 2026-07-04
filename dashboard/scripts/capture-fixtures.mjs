// Capture the live Supabase query results behind the dashboard API routes into
// static JSON fixtures (dashboard/lib/fixtures/). Run once while the database is
// still up; the demo routes replay these forever with no external calls.
//
//   node scripts/capture-fixtures.mjs        (reads DATABASE_URL from ../.env)
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import pg from "pg";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, "..", "lib", "fixtures");

let dbUrl = process.env.DATABASE_URL;
if (!dbUrl) {
  const env = readFileSync(join(here, "..", "..", ".env"), "utf8");
  dbUrl = env.match(/^DATABASE_URL=(.+)$/m)?.[1]?.trim();
}
if (!dbUrl) throw new Error("DATABASE_URL not found in env or ../.env");

const client = new pg.Client({ connectionString: dbUrl, ssl: { rejectUnauthorized: false } });
await client.connect();
const q = async (sql) => (await client.query(sql)).rows;

// --- /api/snapshot ---
const snapshot = {
  leaderboard: await q(`select rank, handle, region, captured_savings, theoretical_optimal,
        pct_of_optimal, fleet_capacity_kwh
      from mart_leaderboard order by rank`),
  pnl: (await q(`select revenue_share, grid_services, costs, net, customer_count
      from mart_company_pnl where sim_day is null`))[0] ?? null,
  pnlDays: await q(`select sim_day, revenue_share, grid_services, costs, net, customer_count
      from mart_company_pnl where sim_day is not null order by sim_day`),
  fleet: (await q(`select total_capacity_kwh, flexible_kw, available_shift_kwh, customer_count
      from mart_fleet where region is null`))[0] ?? null,
  support: (await q(`select open_tickets, escalated, avg_response_secs, oldest_open_age_secs
      from mart_support limit 1`))[0] ?? null,
  activity: await q(`select id, agent, action, rationale, sim_time, created_at
      from decisions_log order by created_at desc limit 40`),
  approvals: await q(`select id, requested_by, action_type, status, created_at
      from pending_approvals where status = 'pending' order by created_at desc`),
  sim: (await q(`select max(window_end) as sim_now,
        min(window_start) as sim_start,
        count(distinct date(window_start)) as sim_days,
        (select coalesce(sum(captured_savings), 0) from mart_leaderboard) as customer_savings
      from benchmarks`))[0] ?? null,
};

// --- /api/pnl ---
const pnl = {
  total: (await q(`select revenue_share, grid_services, costs, net, customer_count
      from mart_company_pnl where sim_day is null`))[0] ?? null,
  days: await q(`select sim_day, revenue_share, grid_services, costs, net, customer_count
      from mart_company_pnl where sim_day is not null order by sim_day`),
  costs: await q(`select sim_time::date::text as sim_day, amount, note
      from ledger where entry_type = 'cost' order by sim_time`),
};

// --- /api/tasks ---
const tasks = {
  tasks: await q(`select id, title, phase, category, status, priority,
        created_by_type, created_by_name, assigned_to,
        completed_by_type, completed_by_name,
        created_at, started_at, completed_at
      from tasks
      order by coalesce(completed_at, started_at, created_at) desc, id desc
      limit 80`),
};

await client.end();

mkdirSync(outDir, { recursive: true });
const save = (name, data) => {
  writeFileSync(join(outDir, name), JSON.stringify(data, null, 1) + "\n");
  console.log(`wrote lib/fixtures/${name}`);
};
save("snapshot.json", snapshot);
save("pnl.json", pnl);
save("tasks.json", tasks);
