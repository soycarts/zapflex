import { Decision } from "@/lib/types";
import { ago } from "@/lib/format";

export default function ActivityFeed({ items }: { items: Decision[] }) {
  return (
    <div className="panel">
      <h2>Agent activity · decisions_log</h2>
      {items.length === 0 ? (
        <div className="empty">No agent decisions yet — the swarm goes live day-of.</div>
      ) : (
        <div className="feed">
          {items.map((d) => (
            <div className="feed-item" key={d.id}>
              <div>
                <span className="who">{d.agent}</span>{" "}
                <span style={{ color: "var(--muted)", fontSize: 12 }}>· {ago(d.created_at)}</span>
              </div>
              <div className="what">
                <strong style={{ color: "var(--text)" }}>{d.action}</strong>
                {d.rationale ? ` — ${d.rationale}` : ""}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
