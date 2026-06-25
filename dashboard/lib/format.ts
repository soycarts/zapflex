export function gbp(v: string | number | null | undefined): string {
  const n = Number(v ?? 0);
  return `£${n.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function pct(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${(Number(v) * 100).toFixed(1)}%`;
}

export function num(v: string | number | null | undefined, dp = 1): string {
  if (v === null || v === undefined) return "—";
  return Number(v).toLocaleString("en-GB", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return `${Math.floor(secs)}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

export function duration(secs: string | number | null | undefined): string {
  if (secs === null || secs === undefined) return "—";
  const s = Number(secs);
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
}

// Sim clock dates are date-only in intent; format in UTC so the calendar day
// never shifts under the viewer's local timezone.
export function simDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-GB", {
    timeZone: "UTC",
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

// Actor → colour for the prep-to-live timeline.
export function actorColor(type: string | null | undefined): string {
  switch (type) {
    case "human":
      return "var(--human)";
    case "claude_code":
      return "var(--claude)";
    case "cursor":
      return "var(--cursor)";
    case "agent":
      return "var(--agent)";
    default:
      return "var(--muted)";
  }
}
