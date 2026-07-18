-- Adds report storage to tracking_requests.
-- Run this in the Supabase SQL editor (Dashboard -> SQL Editor -> New query).
--
-- `status` already exists on this table with a check constraint of
-- ('pending' | 'active' | 'completed' | 'cancelled') -- see sql/supabase_schema.sql.
-- generate_report.py sets it to 'completed' once a report is written.
--
-- `report_text`          : the AI-generated recommendation (plain text).
-- `report_generated_at`  : when the report was produced.

alter table public.tracking_requests
  add column if not exists report_text text,
  add column if not exists report_generated_at timestamptz;
