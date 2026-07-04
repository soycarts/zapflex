export type LeaderRow = {
  rank: number;
  handle: string;
  region: string;
  captured_savings: string;
  theoretical_optimal: string;
  pct_of_optimal: string | null;
  fleet_capacity_kwh: string;
};

export type PnlDay = {
  sim_day: string;
  revenue_share: string;
  grid_services: string;
  costs: string;
  net: string;
  customer_count: number;
};

export type CostEntry = {
  sim_day: string | null;
  amount: string;
  note: string | null;
};

export type Pnl = {
  revenue_share: string;
  grid_services: string;
  costs: string;
  net: string;
  customer_count: number;
} | null;

export type Fleet = {
  total_capacity_kwh: string;
  flexible_kw: string;
  available_shift_kwh: string;
  customer_count: number;
} | null;

export type Support = {
  open_tickets: number;
  escalated: number;
  avg_response_secs: string | null;
  oldest_open_age_secs: string | null;
} | null;

export type Task = {
  id: number;
  title: string;
  phase: string | null;
  category: string | null;
  status: string;
  priority: number | null;
  created_by_type: string | null;
  created_by_name: string | null;
  assigned_to: string | null;
  completed_by_type: string | null;
  completed_by_name: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type Decision = {
  id: number;
  agent: string;
  action: string;
  rationale: string | null;
  sim_time: string | null;
  created_at: string;
};

export type Approval = {
  id: number;
  requested_by: string;
  action_type: string;
  status: string;
  created_at: string;
};

export type Sim = {
  sim_now: string | null;
  sim_start: string | null;
  sim_days: number;
  customer_savings: string;
} | null;

export type Snapshot = {
  leaderboard: LeaderRow[];
  pnl: Pnl;
  pnlDays: PnlDay[];
  fleet: Fleet;
  support: Support;
  activity: Decision[];
  approvals: Approval[];
  sim: Sim;
  ts: number;
  error?: string;
};
