"""Delete expired, unconfirmed email-verification links.

Runs daily (.github/workflows/cleanup_pending_verifications.yml). Confirmed
links are already deleted immediately by app/api/verify/[token]/route.ts once
clicked, so this only ever cleans up links nobody clicked before they expired
(default 30 min, see VERIFICATION_EXPIRY_MINUTES in app/api/requests/route.ts).
A stray row sitting for up to ~24h is harmless (tiny table, free-tier
storage), so a daily cadence is more than sufficient.

Secrets (GitHub Actions):
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")

if not (SUPABASE_URL and SUPABASE_KEY):
    raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")
sb = create_client(SUPABASE_URL, SUPABASE_KEY)


def main():
    now = datetime.now(timezone.utc).isoformat()
    deleted = (
        sb.table("pending_verifications")
        .delete()
        .is_("confirmed_at", "null")
        .lt("expires_at", now)
        .execute()
        .data
    ) or []
    print(f"Deleted {len(deleted)} expired, unconfirmed verification link(s).")


if __name__ == "__main__":
    main()
