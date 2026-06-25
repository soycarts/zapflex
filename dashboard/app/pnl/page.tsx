"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CostEntry, Pnl, PnlDay } from "@/lib/types";
import { PnlChart } from "@/components/PnlChart";
import { gbp } from "@/lib/format";

const POLL_MS = 5000;

type PnlPayload = {
  total: Pnl;
  days: PnlDay[];
  costs: CostEntry[];
  error?: string;
};

export default function PnlPage() {
  const [data, setData] = useState<PnlPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const res = await fetch("/api/pnl", { cache: "no-store" });
        const d: PnlPayload = await res.json();
        if (!alive) return;
        if (d.error) setErr(d.error);
        else {
          setErr(null);
          setData(d);
        }
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : String(e));
      }
    }
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const total = data?.total ?? null;
  const net = Number(total?.net ?? 0);
  const costs = data?.costs ?? [];

  return (
    <div className="wrap">
      <div className="topbar">
        <div className="brand">
          zapflex<span className="dot">.</span>
        </div>
        <Link href="/" className="navlink">← back to dashboard</Link>
      </div>

      <h1 className="page-title">Company P&amp;L · time series</h1>

      {err && <div className="banner">Data error: {err}</div>}
      {!data && !err && <div className="empty">Loading P&amp;L…</div>}

      {data && (
        <>
          <div className="cards">
            <div className="card">
              <div className="label">Net to date</div>
              <div className="value" style={{ color: net >= 0 ? "var(--accent)" : "var(--bad)" }}>
                {gbp(total?.net)}
              </div>
              <div className="sub">{total?.customer_count ?? 0} customers</div>
            </div>
            <div className="card">
              <div className="label">Revenue share</div>
              <div className="value">{gbp(total?.revenue_share)}</div>
              <div className="sub">20% of customer savings</div>
            </div>
            <div className="card">
              <div className="label">Grid services</div>
              <div className="value">{gbp(total?.grid_services)}</div>
              <div className="sub">flexibility availability</div>
            </div>
            <div className="card">
              <div className="label">Costs</div>
              <div className="value" style={{ color: "var(--bad)" }}>
                {gbp(total?.costs)}
              </div>
              <div className="sub">{costs.length} entries</div>
            </div>
          </div>

          <div className="panel">
            <h2>Daily net &amp; cumulative</h2>
            <PnlChart days={data.days} />
          </div>

          <div className="panel">
            <h2>Cost entries · what dents the net</h2>
            {costs.length === 0 ? (
              <div className="empty">No costs booked.</div>
            ) : (
              <table className="pnl-table">
                <thead>
                  <tr>
                    <th>sim day</th>
                    <th className="r">amount</th>
                    <th>note</th>
                  </tr>
                </thead>
                <tbody>
                  {costs.map((c, i) => (
                    <tr key={i}>
                      <td>{c.sim_day ?? "—"}</td>
                      <td className="r neg">−{gbp(Math.abs(Number(c.amount)))}</td>
                      <td>{c.note ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
