export type LeaderRow = {
  rank: number;
  handle: string;
  region: string;
  captured_savings: string;
  theoretical_optimal: string;
  pct_of_optimal: string | null;
  fleet_capacity_kwh: string;
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

export type Snapshot = {
  leaderboard: LeaderRow[];
  pnl: Pnl;
  pnlDays: { sim_day: string; net: string }[];
  fleet: Fleet;
  support: Support;
  tasks: Task[];
  activity: Decision[];
  approvals: Approval[];
  ts: number;
  error?: string;
};
