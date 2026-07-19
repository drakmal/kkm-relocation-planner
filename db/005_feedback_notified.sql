-- Tracks which feedback rows have already been pushed to Telegram, so the
-- notifier only alerts on NEW reports (and never messages you when there's
-- nothing new). Run in the Supabase SQL editor.
--
-- notified_at: NULL = not yet sent to Telegram; set to a timestamp once the
-- notifier has delivered it.

alter table public.feedback
  add column if not exists notified_at timestamptz;

-- Partial index: fast lookup of the un-notified rows the cron scans each run.
create index if not exists idx_feedback_unnotified
  on public.feedback(created_at)
  where notified_at is null;
