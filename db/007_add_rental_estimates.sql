-- Adds area_price_estimates to tracking_requests.
-- Run this in the Supabase SQL editor (Dashboard -> SQL Editor -> New query).
--
-- Malaysia has no free public rental-price API (see app/lib/rental.ts), so
-- generate_report.py asks Groq for an approximate typical rental range per
-- ranked area (clearly an AI estimate, not a live listing price) and stores it
-- here as {"area name (lowercased)": {"low": 800, "high": 1200}}. The report
-- page shows this next to the Mudah/iProperty links when available.

alter table public.tracking_requests
  add column if not exists area_price_estimates jsonb;
