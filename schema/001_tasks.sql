-- 001_tasks.sql
-- Version-controlled DDL for the tasks table. Already applied to Supabase — do not re-run.
-- Canonical field reference: docs/SPEC.md section 4.

create table if not exists tasks (
  id                  bigint generated always as identity primary key,
  title               text not null,
  description         text,
  phase               text not null default 'prep',         -- prep|live
  category            text not null default 'general',
  status              text not null default 'todo',          -- todo|doing|blocked|done|cancelled
  priority            int not null default 3,
  created_by_type     text,                                  -- human|claude_code|agent
  created_by_name     text,
  assigned_to         text,
  completed_by_type   text,                                  -- human|claude_code|agent
  completed_by_name   text,
  parent_id           bigint references tasks(id),
  result              text,
  source_ref          text,
  created_at          timestamptz not null default now(),
  started_at          timestamptz,
  completed_at        timestamptz
);

create index if not exists tasks_phase_status_idx on tasks (phase, status);
create index if not exists tasks_created_by_type_idx on tasks (created_by_type);

-- Enable realtime for the tasks table so the dashboard and agents get live updates.
alter publication supabase_realtime add table tasks;
