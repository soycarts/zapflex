import { LeaderRow } from "@/lib/types";
import { gbp, pct, num } from "@/lib/format";

export default function Leaderboard({ rows }: { rows: LeaderRow[] }) {
  return (
    <div className="panel">
      <h2>Leaderboard · % of perfect-hindsight optimal</h2>
      {rows.length === 0 ? (
        <div className="empty">No customers yet.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Customer</th>
              <th className="num">Captured</th>
              <th className="num">Optimal</th>
              <th style={{ width: "30%" }}>Skill</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const p = r.pct_of_optimal === null ? 0 : Number(r.pct_of_optimal);
              return (
                <tr key={r.handle}>
                  <td>
                    <span className="rankpill">{r.rank}</span>
                  </td>
                  <td>
                    {r.handle}
                    <span style={{ color: "var(--muted)" }}> · {r.region}</span>
                  </td>
                  <td className="num">{gbp(r.captured_savings)}</td>
                  <td className="num">{gbp(r.theoretical_optimal)}</td>
                  <td>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ fontVariantNumeric: "tabular-nums" }}>
                        {pct(r.pct_of_optimal)}
                      </span>
                    </div>
                    <div className="bar">
                      <span style={{ width: `${Math.min(100, p * 100)}%` }} />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
