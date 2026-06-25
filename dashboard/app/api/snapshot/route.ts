import { NextResponse } from "next/server";
import { q } from "@/lib/db";

// Always fresh: the dashboard polls this for the live state of the swarm.
export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET() {
  try {
    const [leaderboard, pnlTotal, pnlDays, fleet, support, tasks, activity, approvals] =
      await Promise.all([
        q(`select rank, handle, region, captured_savings, theoretical_optimal,
                  pct_of_optimal, fleet_capacity_kwh
           from mart_leaderboard order by rank`),
        q(`select revenue_share, grid_services, costs, net, customer_count
           from mart_company_pnl where sim_day is null`),
        q(`select sim_day, net from mart_company_pnl where sim_day is not null order by sim_day`),
        q(`select total_capacity_kwh, flexible_kw, available_shift_kwh, customer_count
           from mart_fleet where region is null`),
        q(`select open_tickets, escalated, avg_response_secs, oldest_open_age_secs
           from mart_support limit 1`),
        q(`select id, title, phase, category, status, priority,
                  created_by_type, created_by_name, assigned_to,
                  completed_by_type, completed_by_name,
                  created_at, started_at, completed_at
           from tasks order by coalesce(completed_at, started_at, created_at), id`),
        q(`select id, agent, action, rationale, sim_time, created_at
           from decisions_log order by created_at desc limit 40`),
        q(`select id, requested_by, action_type, status, created_at
           from pending_approvals where status = 'pending' order by created_at desc`),
      ]);

    return NextResponse.json({
      leaderboard,
      pnl: pnlTotal[0] ?? null,
      pnlDays,
      fleet: fleet[0] ?? null,
      support: support[0] ?? null,
      tasks,
      activity,
      approvals,
      ts: Date.now(),
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
