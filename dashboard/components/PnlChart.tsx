"use client";

import { useState } from "react";
import { PnlDay } from "@/lib/types";
import { gbp } from "@/lib/format";

const ACCENT = "var(--accent)";
const BAD = "var(--bad)";

function cumulative(days: PnlDay[]): number[] {
  let run = 0;
  return days.map((d) => (run += Number(d.net)));
}

// Tiny inline trend of cumulative net for the stat card. Coloured by where it ends up.
export function Sparkline({ days }: { days: PnlDay[] }) {
  if (days.length < 2) return null;
  const W = 200;
  const H = 40;
  const cum = cumulative(days);
  const lo = Math.min(0, ...cum);
  const hi = Math.max(0, ...cum);
  const span = hi - lo || 1;
  const x = (i: number) => (i / (cum.length - 1)) * W;
  const y = (v: number) => H - ((v - lo) / span) * H;
  const line = cum.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  const color = cum[cum.length - 1] >= 0 ? ACCENT : BAD;
  const area = `0,${y(0)} ${line} ${W},${y(0)}`;
  return (
    <svg
      className="sparkline"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      aria-hidden
    >
      <polygon points={area} fill={color} opacity={0.12} />
      <line x1="0" y1={y(0)} x2={W} y2={y(0)} stroke="var(--border)" strokeWidth="1" />
      <polyline points={line} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

// Full daily P&L chart: per-day net as bars (small green gains, big red cost days)
// with the cumulative net line overlaid on the same axis, so the path to a negative
// total is legible. Hovering a day reveals its revenue/grid/cost/net breakdown.
export function PnlChart({ days }: { days: PnlDay[] }) {
  const [hover, setHover] = useState<number | null>(null);

  if (days.length < 2) {
    return <div className="empty">Not enough P&amp;L history yet.</div>;
  }

  const W = 920;
  const H = 320;
  const padTop = 16;
  const padBottom = 28;
  const plotH = H - padTop - padBottom;

  const nets = days.map((d) => Number(d.net));
  const cum = cumulative(days);
  const lo = Math.min(0, ...nets, ...cum);
  const hi = Math.max(0, ...nets, ...cum);
  const span = hi - lo || 1;

  const colW = W / days.length;
  const barW = Math.max(1, Math.min(14, colW * 0.6));
  const cx = (i: number) => i * colW + colW / 2;
  const y = (v: number) => padTop + (1 - (v - lo) / span) * plotH;
  const zeroY = y(0);

  const linePts = cum.map((v, i) => `${cx(i)},${y(v)}`).join(" ");

  const h = hover !== null ? days[hover] : null;
  const hoverLeftPct = hover !== null ? (cx(hover) / W) * 100 : 0;

  return (
    <div className="pnl-chart" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="pnl-svg">
        {/* y gridlines: hi, zero, lo */}
        {[hi, 0, lo].map((v, i) => (
          <line
            key={i}
            x1={0}
            y1={y(v)}
            x2={W}
            y2={y(v)}
            stroke="var(--border)"
            strokeWidth={v === 0 ? 1.2 : 0.6}
            strokeDasharray={v === 0 ? undefined : "3 4"}
          />
        ))}

        {/* daily net bars */}
        {nets.map((v, i) => (
          <rect
            key={i}
            x={cx(i) - barW / 2}
            y={Math.min(y(v), zeroY)}
            width={barW}
            height={Math.abs(y(v) - zeroY)}
            fill={v >= 0 ? ACCENT : BAD}
            opacity={hover === null || hover === i ? 0.55 : 0.25}
          />
        ))}

        {/* cumulative net line */}
        <polyline
          points={linePts}
          fill="none"
          stroke="var(--human)"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
        />

        {hover !== null && (
          <line
            x1={cx(hover)}
            y1={padTop}
            x2={cx(hover)}
            y2={H - padBottom}
            stroke="var(--muted)"
            strokeWidth="0.8"
          />
        )}

        {/* hit areas */}
        {days.map((_, i) => (
          <rect
            key={i}
            x={i * colW}
            y={0}
            width={colW}
            height={H}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
          />
        ))}
      </svg>

      <div className="pnl-ylabels">
        <span style={{ top: `${(y(hi) / H) * 100}%` }}>{gbp(hi)}</span>
        <span style={{ top: `${(zeroY / H) * 100}%` }}>£0</span>
        <span style={{ top: `${(y(lo) / H) * 100}%` }}>{gbp(lo)}</span>
      </div>

      <div className="pnl-legend">
        <span><i style={{ background: "var(--human)" }} /> cumulative net</span>
        <span><i style={{ background: ACCENT }} /> daily gain</span>
        <span><i style={{ background: BAD }} /> daily loss</span>
      </div>

      {h && (
        <div
          className="pnl-tip"
          style={{ left: `${hoverLeftPct}%`, transform: `translateX(${hoverLeftPct > 60 ? "-100%" : "0"})` }}
        >
          <div className="pnl-tip-day">{h.sim_day}</div>
          <div className={Number(h.net) >= 0 ? "pos" : "neg"}>net {gbp(h.net)}</div>
          <div className="muted">cumulative {gbp(cum[hover!])}</div>
          <div className="muted">rev {gbp(h.revenue_share)} · grid {gbp(h.grid_services)}</div>
          {Number(h.costs) > 0 && <div className="neg">cost −{gbp(h.costs)}</div>}
        </div>
      )}
    </div>
  );
}
