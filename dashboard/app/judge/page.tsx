"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { JudgeGrid, interp, MODELS } from "@/lib/judge";
import { LeaderRow } from "@/lib/types";
import { gbp, pct } from "@/lib/format";

const POLL_MS = 4000;

export default function JudgePage() {
  const [grid, setGrid] = useState<JudgeGrid | null>(null);
  const [fleet, setFleet] = useState<LeaderRow[]>([]);
  const [arch, setArch] = useState<string>("ev_solar");
  const [sk, setSk] = useState(1);
  const [ek, setEk] = useState(0);

  // Static precomputed surface (real executor output).
  useEffect(() => {
    fetch("/judge_grid.json")
      .then((r) => r.json())
      .then((g: JudgeGrid) => setGrid(g))
      .catch(() => {});
  }, []);

  // Live fleet leaderboard to rank against.
  useEffect(() => {
    let alive = true;
    const load = () =>
      fetch("/api/snapshot", { cache: "no-store" })
        .then((r) => r.json())
        .then((d) => alive && setFleet(d.leaderboard ?? []))
        .catch(() => {});
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const a = grid?.archetypes[arch];
  const steps = grid?.steps ?? [];

  const judgePct = useMemo(() => {
    if (!a) return 0;
    return interp(a.pct, steps, a.has_solar ? sk : 0, a.has_ev ? ek : 0);
  }, [a, steps, sk, ek]);

  const judgeCaptured = useMemo(() => {
    if (!a) return 0;
    return interp(a.captured, steps, a.has_solar ? sk : 0, a.has_ev ? ek : 0);
  }, [a, steps, sk, ek]);

  // Merge the judge into the live fleet and rank by pct.
  const ranked = useMemo(() => {
    const rows = fleet.map((r) => ({
      handle: r.handle,
      pct: r.pct_of_optimal === null ? 0 : Number(r.pct_of_optimal) * 100,
      you: false,
    }));
    rows.push({ handle: "you (sandbox)", pct: judgePct, you: true });
    rows.sort((x, y) => y.pct - x.pct);
    return rows;
  }, [fleet, judgePct]);

  const myRank = ranked.findIndex((r) => r.you) + 1;

  function applyModel(name: keyof typeof MODELS) {
    setSk(MODELS[name].solar);
    setEk(MODELS[name].ev);
  }

  return (
    <div className="wrap">
      <div className="topbar">
        <div className="brand">
          zapflex<span className="dot">.</span> <span style={{ color: "var(--muted)", fontWeight: 400, fontSize: 15 }}>judge sandbox</span>
        </div>
        <Link href="/" className="navlink">← live dashboard</Link>
      </div>

      <p style={{ color: "var(--muted)", marginBottom: 18, maxWidth: 720 }}>
        Prices are fully visible, so the only edge is forecasting the household. Pick a home,
        then teach the agent its routine — watch your share of the perfect-hindsight optimum
        (and your rank) climb. The numbers come from the real policy executor.
      </p>

      {!grid && <div className="empty">Loading sandbox…</div>}

      {grid && a && (
        <div className="grid">
          <div>
            <div className="panel">
              <h2>1 · Pick a household</h2>
              <div className="choices">
                {Object.entries(grid.archetypes).map(([key, av]) => (
                  <button
                    key={key}
                    className={`choice ${key === arch ? "on" : ""}`}
                    onClick={() => setArch(key)}
                  >
                    {av.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="panel">
              <h2>2 · Tune the forecast model</h2>
              <div className="choices" style={{ marginBottom: 16 }}>
                {Object.keys(MODELS).map((m) => (
                  <button key={m} className="choice ghost" onClick={() => applyModel(m as keyof typeof MODELS)}>
                    {MODELS[m].label}
                  </button>
                ))}
              </div>

              <Slider
                label="Solar forecast knowledge"
                hint={a.has_solar ? "clear-sky → climatological mean" : "no solar on this home"}
                value={sk}
                disabled={!a.has_solar}
                onChange={setSk}
              />
              <Slider
                label="EV routine knowledge"
                hint={a.has_ev ? "ignore EV → anticipate the charging routine" : "no EV on this home"}
                value={ek}
                disabled={!a.has_ev}
                onChange={setEk}
              />
            </div>
          </div>

          <div>
            <div className="panel">
              <h2>Your result · {grid.window_start} → {grid.window_end}</h2>
              <div className="bigpct" style={{ color: "var(--accent)" }}>{judgePct.toFixed(1)}%</div>
              <div style={{ color: "var(--muted)", marginBottom: 12 }}>of the perfect-hindsight optimum</div>
              <div className="bar" style={{ height: 10 }}>
                <span style={{ width: `${Math.min(100, judgePct)}%` }} />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 10 }}>
                <span>captured <strong>{gbp(judgeCaptured)}</strong></span>
                <span style={{ color: "var(--muted)" }}>optimal {gbp(a.oracle_gbp)}</span>
              </div>
            </div>

            <div className="panel">
              <h2>Leaderboard · you vs the live fleet</h2>
              <table>
                <thead>
                  <tr><th>#</th><th>Customer</th><th className="num">% optimal</th></tr>
                </thead>
                <tbody>
                  {ranked.map((r, i) => (
                    <tr key={r.handle} style={r.you ? { background: "rgba(63,185,80,0.10)" } : undefined}>
                      <td><span className="rankpill">{i + 1}</span></td>
                      <td style={r.you ? { fontWeight: 700, color: "var(--accent)" } : undefined}>{r.handle}</td>
                      <td className="num">{r.pct.toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ color: "var(--muted)", marginTop: 10, fontSize: 13 }}>
                You rank <strong style={{ color: "var(--text)" }}>#{myRank}</strong> of {ranked.length}.
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Slider({
  label,
  hint,
  value,
  disabled,
  onChange,
}: {
  label: string;
  hint: string;
  value: number;
  disabled?: boolean;
  onChange: (v: number) => void;
}) {
  return (
    <div className={`slider ${disabled ? "off" : ""}`}>
      <div className="slider-head">
        <span>{label}</span>
        <span className="slider-val">{disabled ? "—" : `${Math.round(value * 100)}%`}</span>
      </div>
      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(parseFloat(e.target.value))}
      />
      <div className="slider-hint">{hint}</div>
    </div>
  );
}
