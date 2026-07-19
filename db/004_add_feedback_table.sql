-- Feedback / "can't find my facility" reports.
-- Run this in the Supabase SQL editor (Dashboard -> SQL Editor -> New query).
--
-- Users submit these from the app; you (admin) read them in the Supabase Table
-- Editor (Tables -> feedback, newest first). No email/domain needed.
--
--   kind           : 'missing_facility' | 'issue' | 'contact'
--   facility_query : the facility they searched for / couldn't find (optional)
--   message        : their report text (required)
--   user_email     : optional, only if they want a reply
--   page_context   : optional JSON (e.g. the boxes they had selected)
--   ip_address     : best-effort, for light spam rate-limiting

create extension if not exists "uuid-ossp";

create table if not exists public.feedback (
  id uuid primary key default uuid_generate_v4(),
  kind text not null default 'issue',
  facility_query text,
  message text not null,
  user_email text,
  page_context jsonb,
  ip_address text,
  created_at timestamptz not null default now()
);

create index if not exists idx_feedback_created_at on public.feedback(created_at desc);
