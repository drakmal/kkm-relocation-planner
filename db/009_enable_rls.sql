-- 009: Enable Row Level Security on all public tables.
--
-- Why: Supabase flagged `rls_disabled_in_public` as CRITICAL (26 Jul 2026) —
-- with RLS off, the anon/public API key (by design not a secret) can read,
-- edit, and delete every row in any public table directly via the REST API,
-- completely bypassing the app (email-domain gate, CAPTCHA, rate limits,
-- magic-link verification all live in app/api/*, not in the DB).
--
-- Fix: enable RLS with NO policies on every table. This blocks all access
-- from the anon/authenticated PostgREST roles by default. It does NOT break
-- the app: every app code path (app/api/*/route.ts, python_scripts/*) uses
-- SUPABASE_SERVICE_ROLE_KEY, and the service_role always bypasses RLS.
-- No client component talks to Supabase directly (verified: no "use client"
-- file imports supabase/createClient), so a default-deny policy set is safe.
--
-- Run this in the Supabase SQL Editor (Project: soxyqhgtzntrbinhcrdv).

alter table public.users enable row level security;
alter table public.locations enable row level security;
alter table public.tracking_requests enable row level security;
alter table public.origin_anchors enable row level security;
alter table public.daily_traffic_logs enable row level security;
alter table public.analysis_reports enable row level security;
alter table public.feedback enable row level security;
alter table public.pending_verifications enable row level security;
