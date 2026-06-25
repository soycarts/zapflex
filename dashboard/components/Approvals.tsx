import { Approval } from "@/lib/types";
import { ago } from "@/lib/format";

export default function Approvals({ items }: { items: Approval[] }) {
  return (
    <div className="panel">
      <h2>
        Pending approvals · the human gate
        {items.length > 0 && (
          <span className="tag in_progress">{items.length} waiting</span>
        )}
      </h2>
      {items.length === 0 ? (
        <div className="empty">Nothing waiting on the gate.</div>
      ) : (
        <div className="feed">
          {items.map((a) => (
            <div className="feed-item" key={a.id} style={{ borderLeftColor: "var(--warn)" }}>
              <div>
                <span className="who">{a.action_type}</span>{" "}
                <span style={{ color: "var(--muted)", fontSize: 12 }}>
                  · {a.requested_by} · {ago(a.created_at)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
