-- Email-ownership verification for tracking requests ("magic link").
-- Run this in the Supabase SQL editor (Dashboard -> SQL Editor -> New query).
--
-- POST /api/requests no longer inserts directly into tracking_requests.
-- Instead it creates a row here and emails a confirmation link; only when the
-- user clicks it (POST /api/verify/[token]) does a real tracking_requests row
-- get created. This keeps unconfirmed submissions invisible to every
-- background job that scans tracking_requests (collect_traffic.py,
-- run_pending_anchors.py, run_due_reports.py all treat any non-'completed'
-- status as "still needs processing" -- an unconfirmed row living there would
-- start burning paid Google Distance Matrix calls before anyone proved they
-- own the inbox).
--
--   token         : unguessable secret embedded in the magic-link URL
--   email         : the @moh.gov.my address to verify
--   ip_address    : best-effort, mirrors tracking_requests' IP capture
--   payload       : the full original POST body (everything needed to create
--                   the tracking_requests row once confirmed)
--   expires_at    : created_at + 30 minutes; unconfirmed rows past this are
--                   deleted by python_scripts/cleanup_pending_verifications.py
--   confirmed_at  : set right before the row is deleted at confirm-time
--                   (kept nullable/set-then-delete rather than relied upon,
--                   since confirmed rows are removed immediately)

create extension if not exists "uuid-ossp";

create table if not exists public.pending_verifications (
  id uuid primary key default uuid_generate_v4(),
  token text not null unique,
  email text not null,
  ip_address text,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  confirmed_at timestamptz
);

create unique index if not exists idx_pending_verifications_token on public.pending_verifications(token);
create index if not exists idx_pending_verifications_email on public.pending_verifications(email);
create index if not exists idx_pending_verifications_expires_at on public.pending_verifications(expires_at) where confirmed_at is null;
