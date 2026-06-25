"use client";

import { useEffect, useRef, useState } from "react";
import { Task } from "@/lib/types";
import { actorColor, ago } from "@/lib/format";

// Self-polling live view of the Supabase tasks table. Polls fast on its own cadence
// (independent of the heavier snapshot), diffs against the previous poll to flag new
// tasks and status transitions, and animates them so the board feels realtime.
const POLL_MS = 2000;
const FLASH_MS = 2600;

type UiStatus = "todo" | "doing" | "blocked" | "done" | "cancelled";

function uiStatus(s: string | null | undefined): UiStatus {
  switch (s) {
    case "doing":
    case "in_progress":
      return "doing";
    case "done":
      return "done";
    case "blocked":
      return "blocked";
    case "cancelled":
    case "canceled":
      return "cancelled";
    default:
      return "todo";
  }
}

const STATUS_LABEL: Record<UiStatus, string> = {
  todo: "created",
  doing: "in progress",
  blocked: "blocked",
  done: "completed",
  cancelled: "cancelled",
};

function StatusIcon({ status }: { status: UiStatus }) {
  switch (status) {
    case "done":
      return (
        <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
          <circle cx="8" cy="8" r="7" fill="currentColor" />
          <path
            d="M4.6 8.2l2.2 2.2 4.6-4.8"
            fill="none"
            stroke="var(--bg)"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    case "doing":
      return (
        <svg viewBox="0 0 16 16" width="16" height="16" className="spin" aria-hidden>
          <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="2" opacity="0.25" />
          <path
            d="M8 2a6 6 0 0 1 6 6"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          />
        </svg>
      );
    case "blocked":
      return (
        <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
          <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <line x1="8" y1="4.5" x2="8" y2="8.8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          <circle cx="8" cy="11.3" r="0.9" fill="currentColor" />
        </svg>
      );
    case "cancelled":
      return (
        <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
          <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      );
    default:
      return (
        <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
          <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="2.2 2.2" />
        </svg>
      );
  }
}

function took(created: string, completed: string): string {
  const s = Math.max(0, (new Date(completed).getTime() - new Date(created).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

type Flash = "new" | "changed";

export default function TaskBoard() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [flash, setFlash] = useState<Record<number, Flash>>({});

  // Previous status per task id, plus whether we've seeded the baseline yet (so the
  // first load doesn't flash every task as "new").
  const prev = useRef<Map<number, UiStatus>>(new Map());
  const seeded = useRef(false);
  const timers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  useEffect(() => {
    let alive = true;

    function markFlash(id: number, kind: Flash) {
      setFlash((f) => ({ ...f, [id]: kind }));
      const existing = timers.current.get(id);
      if (existing) clearTimeout(existing);
      const t = setTimeout(() => {
        if (!alive) return;
        setFlash((f) => {
          const next = { ...f };
          delete next[id];
          return next;
        });
        timers.current.delete(id);
      }, FLASH_MS);
      timers.current.set(id, t);
    }

    async function load() {
      try {
        const res = await fetch("/api/tasks", { cache: "no-store" });
        const data: { tasks?: Task[]; error?: string } = await res.json();
        if (!alive) return;
        if (data.error) {
          setErr(data.error);
          return;
        }
        setErr(null);
        const next = data.tasks ?? [];

        if (seeded.current) {
          for (const t of next) {
            const before = prev.current.get(t.id);
            const now = uiStatus(t.status);
            if (before === undefined) markFlash(t.id, "new");
            else if (before !== now) markFlash(t.id, "changed");
          }
        }
        prev.current = new Map(next.map((t) => [t.id, uiStatus(t.status)]));
        seeded.current = true;

        setTasks(next);
        setLoaded(true);
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : String(e));
      }
    }

    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
      timers.current.forEach((t) => clearTimeout(t));
      timers.current.clear();
    };
  }, []);

  const counts = tasks.reduce<Record<UiStatus, number>>(
    (acc, t) => {
      acc[uiStatus(t.status)] += 1;
      return acc;
    },
    { todo: 0, doing: 0, blocked: 0, done: 0, cancelled: 0 }
  );

  return (
    <div className="panel">
      <h2>
        Live task board · tasks
        <span className="legend">
          <span><i style={{ background: "var(--human)" }} /> human</span>
          <span><i style={{ background: "var(--claude)" }} /> claude</span>
          <span><i style={{ background: "var(--cursor)" }} /> cursor</span>
          <span><i style={{ background: "var(--agent)" }} /> agent</span>
        </span>
      </h2>

      <div className="tb-summary">
        <span className="tb-sum st-doing">{counts.doing} in progress</span>
        <span className="tb-sum st-todo">{counts.todo} queued</span>
        <span className="tb-sum st-done">{counts.done} done</span>
        {counts.blocked > 0 && <span className="tb-sum st-blocked">{counts.blocked} blocked</span>}
      </div>

      {err && <div className="empty">tasks unavailable: {err}</div>}
      {!loaded && !err && <div className="empty">Loading tasks…</div>}
      {loaded && tasks.length === 0 && !err && (
        <div className="empty">No tasks yet — the board fills as the swarm works.</div>
      )}

      <div className="taskboard">
        {tasks.map((t) => {
          const status = uiStatus(t.status);
          const f = flash[t.id];
          const creatorColor = actorColor(t.created_by_type);
          return (
            <div key={t.id} className={`task-row ${status}${f ? ` ${f}` : ""}`}>
              <span className={`st st-${status}`}>
                <StatusIcon status={status} />
              </span>
              <div className="task-main">
                <div className="task-title">{t.title}</div>
                <div className="task-sub">
                  {t.created_by_name && (
                    <span
                      className="chip"
                      style={{ color: creatorColor, borderColor: creatorColor }}
                    >
                      {t.created_by_name}
                    </span>
                  )}
                  <span>created {ago(t.created_at)}</span>
                  {status === "done" && t.completed_at && (
                    <span>
                      · done {ago(t.completed_at)} · took {took(t.created_at, t.completed_at)}
                      {t.completed_by_name && t.completed_by_name !== t.created_by_name
                        ? ` by ${t.completed_by_name}`
                        : ""}
                    </span>
                  )}
                  {status === "doing" && t.started_at && (
                    <span>· started {ago(t.started_at)}</span>
                  )}
                </div>
              </div>
              <span className={`tag ${status}`}>{STATUS_LABEL[status]}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
