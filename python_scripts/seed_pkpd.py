"""
Seed PKPD (Pejabat Kesihatan Pergigian Daerah = District Dental Health Office),
one per district, by mirroring the existing PKD district offices. Each district
has both a PKD and a PKPD; they sit at the same district town, so the PKD
coordinate is a good approximation for the PKPD.

tier='district', metadata.category='pkpd', name 'PKPD <district>'.

Run:  python python_scripts/seed_pkpd.py
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(Path(__file__).resolve().parents[1] / ".env.local")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
sb = create_client(os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
PARENT = "parent_location_id"


def main():
    # Existing PKD offices.
    pkds, start = [], 0
    while True:
        rows = sb.table("locations").select("*").eq("tier", "district").like("name", "PKD %").range(start, start + 999).execute().data
        pkds += rows
        if len(rows) < 1000:
            break
        start += 1000

    # Skip any PKPD already present (idempotent).
    existing = sb.table("locations").select("name").eq("tier", "district").like("name", "PKPD %").execute().data or []
    have = {r["name"] for r in existing}

    new_rows = []
    for p in pkds:
        district = p["name"][4:]  # strip "PKD "
        name = f"PKPD {district}"
        if name in have:
            continue
        new_rows.append({
            "name": name, "tier": "district", PARENT: p[PARENT],
            "latitude": p["latitude"], "longitude": p["longitude"],
            "address": p.get("address"), "metadata": {"kind": "pkpd", "category": "pkpd", "district": district},
        })

    for i in range(0, len(new_rows), 200):
        sb.table("locations").insert(new_rows[i:i + 200]).execute()

    print(f"PKD offices: {len(pkds)}  |  inserted PKPD: {len(new_rows)}")


if __name__ == "__main__":
    main()
