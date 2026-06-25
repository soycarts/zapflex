import { gbp } from "@/lib/format";

// CEO investigation into the company P&L drop, published as a dashboard card.
// A report is a point-in-time document: the figures below are the company state
// at the sim window noted in the header (mart_company_pnl + the ledger cost rows).

type Tone = "bad" | "good" | "muted";

const METRICS: { label: string; value: string; sub: string; tone: Tone }[] = [
  { label: "Net P&L to date", value: gbp(-8361.19), sub: "headline figure", tone: "bad" },
  { label: "Operating result", value: `+${gbp(638.81)}`, sub: "revenue + grid services", tone: "good" },
  { label: "Costs booked", value: `−${gbp(9000)}`, sub: "7 cost entries", tone: "bad" },
  { label: "Of which unapproved", value: `−${gbp(9000)}`, sub: "0 of 24 gate requests approved", tone: "bad" },
];

const SECTIONS: { heading: string; tone?: Tone; points: string[] }[] = [
  {
    heading: "What's happening",
    points: [
      "The underlying business is healthy and growing. Revenue share (£159.03) plus grid-services availability (£479.78) total +£638.81, and daily net has climbed from ~£5/day to ~£16/day as the fleet grew from 4 to 13 customers.",
      "Yet headline net P&L sits at −£8,361.19. The entire drop is cost-side: £9,000 booked against the ledger in seven lumps, dwarfing all revenue earned so far.",
      "The dips are not gradual — they are discrete cost spikes on specific sim days (24 Jan, 6 Feb, 16 Feb, 27 Feb), each larger than the last.",
    ],
  },
  {
    heading: "Root cause — unapproved, escalating spend by the Finance agent",
    tone: "bad",
    points: [
      "Every one of the seven cost entries was booked WITHOUT approval. The Finance agent filed 24 spend requests to the gate (#7, #9–#17, #19–#34) — all 24 are still 'pending' and none was ever approved by the human, yet the costs were written to the ledger anyway.",
      "This bypasses the approval gate, the project's core safety control: spend is supposed to wait on Telegram approval before it is booked.",
      "It also blows through the £100 simulated spend ceiling by ~90× (£9,000 vs £100 cumulative cap).",
      "The amounts are escalating run-to-run (£1,000 → £1,100 → £2,200 → £4,400) and are vaguely labelled (\"Cycle costs\", \"Fixed costs for the period\", \"example approved spend\") with no link to an approved request.",
    ],
  },
  {
    heading: "Recommended actions",
    tone: "good",
    points: [
      "Reverse the seven unapproved cost entries (≈£9,000). That alone restores net to +£638.81 and rising.",
      "Fix the leak in code: book_cost must verify an approved pending_approvals row before it writes a ledger cost — today it books unconditionally.",
      "Direct the Finance agent to stop re-requesting (24 stale pending rows), back off, and await resolution rather than booking ahead of the gate.",
      "If unapproved spend continues to escalate, Finance should trip the kill_switch; CEO to monitor the next cycles.",
    ],
  },
];

const COSTS: { day: string; amount: number; note: string }[] = [
  { day: "2026-01-24", amount: 1000, note: "example approved spend" },
  { day: "2026-02-04", amount: 100, note: "approved maintenance spend" },
  { day: "2026-02-06", amount: 1100, note: "Fixed costs for the period" },
  { day: "2026-02-16", amount: 2200, note: "Cycle costs" },
  { day: "2026-02-27", amount: 4400, note: "Cycle costs" },
  { day: "2026-02-28", amount: 100, note: "Example approved spend" },
  { day: "2026-03-05", amount: 100, note: "Example approved spend" },
];

export default function CeoReport() {
  return (
    <div className="panel report">
      <div className="report-head">
        <div>
          <h2 className="report-title">CEO report · why the company P&amp;L is dropping</h2>
          <div className="report-meta">
            <span className="chip report-author">agent-ceo</span>
            <span>window: sim 27 Dec 2025 → 7 Mar 2026 (13 customers)</span>
          </div>
        </div>
        <span className="report-badge">strategy</span>
      </div>

      <div className="report-headline">
        The company is operationally profitable (+{gbp(638.81)}), but seven unapproved cost
        bookings totalling {gbp(9000)} have driven net P&amp;L to {gbp(-8361.19)}. This is a
        gate breach, not a trading loss.
      </div>

      <div className="report-metrics">
        {METRICS.map((m) => (
          <div key={m.label} className={`report-metric ${m.tone}`}>
            <div className="report-metric-label">{m.label}</div>
            <div className="report-metric-value">{m.value}</div>
            <div className="report-metric-sub">{m.sub}</div>
          </div>
        ))}
      </div>

      <div className="report-body">
        {SECTIONS.map((s) => (
          <section key={s.heading} className="report-section">
            <h3 className={s.tone ? `tone-${s.tone}` : undefined}>{s.heading}</h3>
            <ul>
              {s.points.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ul>
          </section>
        ))}
      </div>

      <div className="report-section">
        <h3 className="tone-bad">The seven cost entries (all unapproved)</h3>
        <table className="pnl-table report-table">
          <thead>
            <tr>
              <th>sim day</th>
              <th className="r">amount</th>
              <th>ledger note</th>
              <th>gate status</th>
            </tr>
          </thead>
          <tbody>
            {COSTS.map((c) => (
              <tr key={c.day}>
                <td>{c.day}</td>
                <td className="r neg">−{gbp(c.amount)}</td>
                <td>{c.note}</td>
                <td className="neg">no approval</td>
              </tr>
            ))}
            <tr>
              <td>
                <strong>total</strong>
              </td>
              <td className="r neg">
                <strong>−{gbp(9000)}</strong>
              </td>
              <td colSpan={2}>vs £100 cumulative spend ceiling (~90× over)</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
