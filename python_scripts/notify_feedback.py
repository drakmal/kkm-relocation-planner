"""Push NEW user feedback to Telegram.

Runs on a cron (.github/workflows/notify_feedback.yml). It only messages you when
there is un-notified feedback: if nothing new, it exits silently (no Telegram
message, ever). After a successful send it stamps notified_at so each report is
sent exactly once.

Secrets (GitHub Actions):
  TELEGRAM_BOT_TOKEN  - from @BotFather
  TELEGRAM_CHAT_ID    - your chat id (message @userinfobot to get it)
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

If the Telegram secrets are absent the script skips WITHOUT marking rows notified,
so once you add them any pending feedback is delivered on the next run.
"""

import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not (SUPABASE_URL and SUPABASE_KEY):
    raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")
sb = create_client(SUPABASE_URL, SUPABASE_KEY)


def main():
    rows = (
        sb.table("feedback")
        .select("id, kind, facility_query, message, user_email, created_at")
        .is_("notified_at", "null")
        .order("created_at", desc=False)
        .limit(50)
        .execute()
        .data
    ) or []

    if not rows:
        print("No new feedback — nothing to send.")
        return

    if not (TOKEN and CHAT_ID):
        # Don't mark notified, so these get delivered once secrets are configured.
        print(f"{len(rows)} new report(s) pending, but TELEGRAM_BOT_TOKEN/CHAT_ID are not set — skipping.")
        return

    lines = [f"\U0001F514 KKM Planner: {len(rows)} new feedback report(s)"]
    for r in rows:
        fq = f" — {r['facility_query']}" if r.get("facility_query") else ""
        em = f"\n   reply-to: {r['user_email']}" if r.get("user_email") else ""
        msg = (r.get("message") or "").strip().replace("\n", " ")
        lines.append(f"\n• [{r.get('kind', 'issue')}]{fq}\n   {msg[:300]}{em}")
    text = "\n".join(lines)

    resp = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True},
        timeout=30,
    )
    resp.raise_for_status()  # if this fails, rows stay un-notified and retry next run

    now = datetime.now(timezone.utc).isoformat()
    sb.table("feedback").update({"notified_at": now}).in_("id", [r["id"] for r in rows]).execute()
    print(f"Sent {len(rows)} report(s) to Telegram and marked notified.")


if __name__ == "__main__":
    main()
