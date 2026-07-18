"""
Find tracking requests whose tracking window has ended and generate + email
their report. Report is produced as soon as the last working day has passed the
user's arrive-home time (no fixed wait — Groq generates in seconds).

Intended to run on a schedule (GitHub Actions / cron), e.g. hourly.

Run:  python python_scripts/run_due_reports.py
"""

import re
from datetime import datetime, timezone, timedelta

from generate_report import supabase, process_request

MYT = timezone(timedelta(hours=8))  # Malaysia time


def _time_parts(value: str, default=(9, 0)):
    try:
        hh, mm = (value or "")[:5].split(":")
        return int(hh), int(mm)
    except (ValueError, AttributeError):
        return default


def tracking_end(row: dict) -> datetime:
    """Datetime (MYT) when tracking finishes. Tracking now only covers the
    morning commute, so the window ends at the arrive-office time on the end
    date (stored in tracking_duration as 'YYYY-MM-DD to YYYY-MM-DD')."""
    duration = row.get("tracking_duration") or ""
    hh, mm = _time_parts(row.get("arrival_time"))  # arrival_time = arrive-office
    match = re.search(r"to\s+(\d{4})-(\d{2})-(\d{2})", duration)
    if match:
        y, mo, d = (int(x) for x in match.groups())
        return datetime(y, mo, d, hh, mm, tzinfo=MYT)

    # Legacy fallback: old "N_days" rows -> N working days from created_at.
    legacy = re.match(r"(\d+)_day", duration)
    days = int(legacy.group(1)) if legacy else 1
    day = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")).astimezone(MYT)
    counted = 0
    while counted < max(1, days):
        day = day + timedelta(days=1)
        if day.weekday() < 5:
            counted += 1
    return day.replace(hour=hh, minute=mm, second=0, microsecond=0)


def main():
    rows = (
        supabase.table("tracking_requests")
        .select("id, created_at, tracking_duration, arrival_time, status")
        .neq("status", "completed")
        .execute()
        .data
        or []
    )

    now = datetime.now(MYT)
    due = []
    for r in rows:
        if now >= tracking_end(r):
            due.append(r["id"])

    print(f"{len(rows)} open request(s); {len(due)} due for reporting.")
    for rid in due:
        try:
            process_request(rid)
            print(f"  reported {rid}")
        except Exception as exc:  # noqa: BLE001 - one failure must not block the rest
            print(f"  FAILED {rid}: {exc}")


if __name__ == "__main__":
    main()
