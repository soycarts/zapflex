import { NextResponse } from "next/server";
import { getPnl } from "@/lib/replay";

// Time-series P&L feed for the /pnl detail page, served from the end-of-run
// fixture (lib/replay.ts): company total, daily breakdown, and cost entries.
export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET() {
  return NextResponse.json(getPnl());
}
