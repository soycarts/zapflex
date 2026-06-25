import { NextResponse } from "next/server";
import { q } from "@/lib/db";
import { CostEntry, Pnl, PnlDay } from "@/lib/types";

// Time-series P&L feed for the /pnl detail page: the company total, the daily
// revenue/cost/net breakdown, and the individual cost entries that drive the dips.
export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET() {
  try {
    const [total, days, costs] = await Promise.all([
      q<Pnl>(
        `select revenue_share, grid_services, costs, net, customer_count
         from mart_company_pnl where sim_day is null`
      ),
      q<PnlDay>(
        `select sim_day, revenue_share, grid_services, costs, net, customer_count
         from mart_company_pnl where sim_day is not null order by sim_day`
      ),
      q<CostEntry>(
        `select sim_time::date::text as sim_day, amount, note
         from ledger where entry_type = 'cost' order by sim_time`
      ),
    ]);
    return NextResponse.json({
      total: total[0] ?? null,
      days,
      costs,
      ts: Date.now(),
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
