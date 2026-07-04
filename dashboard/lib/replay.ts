// Demo replay layer: serves captured fixtures (lib/fixtures/, see
// scripts/capture-fixtures.mjs) with timestamps rebased onto a rolling loop, so the
// dashboard looks live forever with zero external calls. The final ~6.5 minutes of
// real agent activity — decisions and task lifecycles — replays on a fixed cycle,
// deterministically derived from wall-clock time (stateless across serverless
// invocations). Aggregates (leaderboard, P&L, fleet, sim clock) stay at their
// end-of-run values.
import snapshotFx from "./fixtures/snapshot.json";
import pnlFx from "./fixtures/pnl.json";
import tasksFx from "./fixtures/tasks.json";
import { Decision, Snapshot, Task } from "./types";

const ms = (iso: string) => new Date(iso).getTime();
const iso = (t: number) => new Date(t).toISOString();

// The replay window: the span of captured decisions_log activity, plus a short
// breather before the loop repeats.
// pg serialised bigint ids as strings, so the live API did too; the fixture
// keeps that shape and the UI only ever uses ids as keys.
const activity = snapshotFx.activity as unknown as Decision[];
const WINDOW_START = Math.min(...activity.map((d) => ms(d.created_at)));
const WINDOW_END = Math.max(...activity.map((d) => ms(d.created_at)));
const CYCLE = WINDOW_END - WINDOW_START + 30_000;

// Most recent occurrence (<= now) of an event that repeats every CYCLE at a fixed
// offset from the window start.
function lastOccurrence(now: number, originalTs: number): number {
  const offset = (originalTs - WINDOW_START) % CYCLE;
  const cycleStart = Math.floor(now / CYCLE) * CYCLE;
  const occ = cycleStart + offset;
  return occ > now ? occ - CYCLE : occ;
}

function replayActivity(now: number): Decision[] {
  return activity
    .map((d) => ({ ...d, created_at: iso(lastOccurrence(now, ms(d.created_at))) }))
    .sort((a, b) => ms(b.created_at) - ms(a.created_at));
}

// Tasks created inside the replay window loop through their real lifecycle
// (created -> in progress -> done) each cycle, under a fresh id per cycle so
// completed copies from the previous pass linger instead of un-completing.
// Older tasks form a static backdrop, held at a constant age beyond the loop.
const allTasks = tasksFx.tasks as unknown as Task[];
const replayTasks = allTasks.filter((t) => ms(t.created_at) >= WINDOW_START);
const staticTasks = allTasks.filter((t) => ms(t.created_at) < WINDOW_START);
const STATIC_NEWEST = Math.max(
  ...staticTasks.map((t) => ms(t.completed_at ?? t.started_at ?? t.created_at))
);

function replayTaskBoard(now: number): Task[] {
  const out: Task[] = [];
  const cycleStart = Math.floor(now / CYCLE) * CYCLE;

  // Current cycle plus the previous one, so the board always shows a full recent
  // history even right after the loop wraps.
  for (const back of [0, 1]) {
    const base = cycleStart - back * CYCLE;
    const cycleNo = Math.floor(base / CYCLE);
    for (const t of replayTasks) {
      const createdAt = base + (ms(t.created_at) - WINDOW_START);
      if (createdAt > now) continue; // not created yet this cycle
      const startedAt = t.started_at ? base + (ms(t.started_at) - WINDOW_START) : null;
      const completedAt = t.completed_at ? base + (ms(t.completed_at) - WINDOW_START) : null;
      const started = startedAt !== null && startedAt <= now;
      const completed = completedAt !== null && completedAt <= now;
      out.push({
        ...t,
        id: Number(t.id) * 10_000 + (cycleNo % 10_000),
        status: completed ? "done" : started ? "doing" : "todo",
        created_at: iso(createdAt),
        started_at: started ? iso(startedAt!) : null,
        completed_at: completed ? iso(completedAt!) : null,
        completed_by_type: completed ? t.completed_by_type : null,
        completed_by_name: completed ? t.completed_by_name : null,
      });
    }
  }

  // Static backdrop: preserve relative spacing, anchored two cycles back so it
  // always sorts below the replayed live work.
  const shift = cycleStart - 2 * CYCLE - STATIC_NEWEST;
  for (const t of staticTasks) {
    out.push({
      ...t,
      created_at: iso(ms(t.created_at) + shift),
      started_at: t.started_at ? iso(ms(t.started_at) + shift) : null,
      completed_at: t.completed_at ? iso(ms(t.completed_at) + shift) : null,
    });
  }

  const key = (t: Task) => ms(t.completed_at ?? t.started_at ?? t.created_at);
  return out.sort((a, b) => key(b) - key(a) || Number(b.id) - Number(a.id)).slice(0, 80);
}

export function getSnapshot(): Snapshot {
  const now = Date.now();
  return {
    ...(snapshotFx as unknown as Snapshot),
    activity: replayActivity(now),
    ts: now,
  };
}

export function getPnl() {
  return { ...pnlFx, ts: Date.now() };
}

export function getTasks() {
  return { tasks: replayTaskBoard(Date.now()), ts: Date.now() };
}
