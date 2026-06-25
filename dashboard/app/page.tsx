"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Snapshot } from "@/lib/types";
import { simDate } from "@/lib/format";
import StatCards from "@/components/StatCards";
import Leaderboard from "@/components/Leaderboard";
import TaskBoard from "@/components/TaskBoard";
import ActivityFeed from "@/components/ActivityFeed";
import Approvals from "@/components/Approvals";
import CeoReport from "@/components/CeoReport";

const POLL_MS = 4000;

export default function Page() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const res = await fetch("/api/snapshot", { cache: "no-store" });
        const data: Snapshot = await res.json();
        if (!alive) return;
        if (data.error) setErr(data.error);
        else {
          setErr(null);
          setSnap(data);
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

  return (
    <div className="wrap">
      <div className="topbar">
        <div className="brand">
          zapflex<span className="dot">.</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          {snap?.sim?.sim_now && (
            <div className="simclock" title="Current simulated date">
              <span className="simclock-label">sim date</span>
              <span className="simclock-date">{simDate(snap.sim.sim_now)}</span>
            </div>
          )}
          <Link href="/judge" className="navlink">judge sandbox →</Link>
          <div className="live">
            <span className="pulse" />
            live · refreshes every {POLL_MS / 1000}s
          </div>
        </div>
      </div>

      {err && <div className="banner">Data error: {err}</div>}

      {!snap && !err && <div className="empty">Loading the swarm…</div>}

      {snap && (
        <>
          <StatCards pnl={snap.pnl} pnlDays={snap.pnlDays} fleet={snap.fleet} support={snap.support} sim={snap.sim} />
          <div className="grid">
            <div>
              <Leaderboard rows={snap.leaderboard} />
              <ActivityFeed items={snap.activity} />
            </div>
            <div>
              <TaskBoard />
              <Approvals items={snap.approvals} />
            </div>
          </div>
          <CeoReport />
        </>
      )}
    </div>
  );
}
