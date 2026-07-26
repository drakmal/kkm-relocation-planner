-- Adds distance_km to daily_traffic_logs.
-- Run this in the Supabase SQL editor (Dashboard -> SQL Editor -> New query).
--
-- Google's Distance Matrix response already includes a `distance` value
-- alongside `duration` -- collect_traffic.py was discarding it. Storing it lets
-- generate_report.py compute an average speed (distance / time) per anchor, a
-- free proxy for "does this route look direct/highway-fed, or indirect and
-- congested" -- without needing a separate paid Routes/Directions API call.

alter table public.daily_traffic_logs
  add column if not exists distance_km double precision;
