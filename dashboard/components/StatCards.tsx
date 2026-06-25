import { Fleet, Pnl, Support } from "@/lib/types";
import { gbp, num, duration } from "@/lib/format";

export default function StatCards({
  pnl,
  fleet,
  support,
}: {
  pnl: Pnl;
  fleet: Fleet;
  support: Support;
}) {
  return (
    <div className="cards">
      <div className="card">
        <div className="label">Company net P&amp;L</div>
        <div className="value" style={{ color: "var(--accent)" }}>
          {gbp(pnl?.net)}
        </div>
        <div className="sub">
          rev {gbp(pnl?.revenue_share)} · grid {gbp(pnl?.grid_services)} · cost{" "}
          {gbp(pnl?.costs)}
        </div>
      </div>
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
