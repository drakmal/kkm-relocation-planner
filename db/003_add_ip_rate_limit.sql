-- Adds IP-based rate limiting to tracking_requests.
-- Run this in the Supabase SQL editor (Dashboard -> SQL Editor -> New query).
--
-- Motivation: the MOH email gate only *format*-checks the address, so a fake
-- @moh.gov.my passes and each fake email gets its own 3/week quota, which can
-- run up paid Google Distance Matrix usage. Capping requests per source IP
-- bounds the blast radius regardless of how many emails an abuser invents.
--
-- The API route (app/api/requests/route.ts) writes `ip_address` best-effort and
-- treats a missing column as "gate disabled", so deploying the code before
-- running this migration is safe -- the cap simply activates once the column
-- exists.

alter table public.tracking_requests
  add column if not exists ip_address text;

create index if not exists idx_tracking_requests_ip_address
  on public.tracking_requests(ip_address);
