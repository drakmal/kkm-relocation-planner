"""
Remove OSM noise from Box 2 (tier='district'): nodes mis-tagged
amenity=hospital that are actually departments, offices, labs, or village
clinics. Conservative DENYLIST only -- anything not clearly non-hospital is
kept, so real (incl. private) hospitals are never removed. PKD rows are never
touched.

Run:  python python_scripts/cleanup_facilities.py
"""

import os
import re
from pathlib import Path
from supabase import create_client


def load_env_file():
    for env_path in [Path(__file__).resolve().parents[1] / ".env.local", Path(__file__).resolve().parents[1] / ".env"]:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip().strip("\"'")


load_env_file()
supabase = create_client(
    os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)

# Delete a district-tier row if its name matches any of these (case-insensitive)
# and it is NOT a PKD office.
DENY = re.compile(
    r"^(jabatan|unit|pejabat|bahagian|makmal|wad|farmasi|dewan|blok|kompleks|stor|klinik)\b"
    r"|radiologi|mediserve|kerja sosial|pusat transfusi|forensik|patologi|makmal",
    re.IGNORECASE,
)


def main():
    rows = supabase.table("locations").select("id, name").eq("tier", "district").execute().data or []
    to_delete = [r for r in rows if not r["name"].lower().startswith("pkd ") and DENY.search(r["name"])]

    print(f"district rows: {len(rows)}  |  removing {len(to_delete)} non-hospital entries")
    for r in to_delete:
        print("  -", r["name"])

    ids = [r["id"] for r in to_delete]
    for i in range(0, len(ids), 100):
        supabase.table("locations").delete().in_("id", ids[i:i + 100]).execute()

    print("Cleanup done.")


if __name__ == "__main__":
    main()
