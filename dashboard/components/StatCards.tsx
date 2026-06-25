import Link from "next/link";
import { Fleet, Pnl, PnlDay, Support } from "@/lib/types";
import { gbp, num, duration } from "@/lib/format";
import { Sparkline } from "@/components/PnlChart";

export default function StatCards({
  pnl,
  pnlDays,
  fleet,
  support,
}: {
  pnl: Pnl;
  pnlDays: PnlDay[];
  fleet: Fleet;
  support: Support;
}) {
  const net = Number(pnl?.net ?? 0);
  return (
    <div className="cards">
      <Link href="/pnl" className="card card-link" aria-label="Open P&L time series">
        <div className="label">
          Company net P&amp;L <span className="card-cta">trend →</span>
        </div>
        <div className="value" style={{ color: net >= 0 ? "var(--accent)" : "var(--bad)" }}>
          {gbp(pnl?.net)}
        </div>
        <div className="sub">
          rev {gbp(pnl?.revenue_share)} · grid {gbp(pnl?.grid_services)} · cost{" "}
          {gbp(pnl?.costs)}
        </div>
        <Sparkline days={pnlDays ?? []} />
      </Link>
      <div className="card">
        <div className="label">Customers</div>
        <div className="value">{pnl?.customer_count ?? fleet?.customer_count ?? 0}</div>
        <div className="sub">active in fleet</div>
      </div>
      <div className="card">
        <div className="label">Flexible capacity</div>
        <div className="value">{num(fleet?.flexible_kw)} kW</div>
        <div className="sub">
          {num(fleet?.total_capacity_kwh)} kWh installed · {num(fleet?.available_shift_kwh)} kWh
          shiftable
        </div>
      </div>
      <div className="card">
        <div className="label">Support</div>
        <div className="value">{support?.open_tickets ?? 0}</div>
        <div className="sub">
          {support?.escalated ?? 0} escalated · oldest {duration(support?.oldest_open_age_secs)}
        </div>
      </div>
    </div>
  );
}
