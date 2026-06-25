export type JudgeArchetype = {
  label: string;
  has_solar: boolean;
  has_ev: boolean;
  oracle_gbp: number;
  pct: number[][]; // pct[solar_idx][ev_idx]
  captured: number[][];
};

export type JudgeGrid = {
  window_start: string;
  window_end: string;
  steps: number[];
  archetypes: Record<string, JudgeArchetype>;
};

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

// Bilinear interpolation over the (solar_knowledge, ev_knowledge) grid.
export function interp(grid: number[][], steps: number[], sk: number, ek: number): number {
  const n = steps.length;
  const lo = (v: number) => {
    let i = 0;
    while (i < n - 1 && steps[i + 1] <= v) i++;
    return Math.min(i, n - 2);
  };
  const si = lo(sk);
  const ei = lo(ek);
  const s0 = steps[si];
  const s1 = steps[si + 1];
  const e0 = steps[ei];
  const e1 = steps[ei + 1];
  const ts = s1 > s0 ? (sk - s0) / (s1 - s0) : 0;
  const te = e1 > e0 ? (ek - e0) / (e1 - e0) : 0;
  const top = lerp(grid[si][ei], grid[si][ei + 1], te);
  const bot = lerp(grid[si + 1][ei], grid[si + 1][ei + 1], te);
  return lerp(top, bot, ts);
}

// Named forecast models map to (solar_knowledge, ev_knowledge), matching energy/forecast.py.
export const MODELS: Record<string, { solar: number; ev: number; label: string }> = {
  naive: { solar: 0, ev: 0, label: "Naive" },
  seasonal: { solar: 1, ev: 0, label: "Seasonal" },
  learned: { solar: 1, ev: 1, label: "Learned" },
};
