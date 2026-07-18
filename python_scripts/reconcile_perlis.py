"""
Reconcile Perlis clinic-tier facilities with the official directories:
- KK: https://perlis.moh.gov.my/edirektori/web/klinikkesihatan (11 clinics)
- KKP: MOH dental directory (remove the district dental OFFICE rows that leaked
  into the clinic tier; those belong in Box 2 as 'PKPD Arau').

Deletes the current messy Perlis KK rows and re-inserts the 11 official ones
(geocoded, parented to PKD Kangar). Removes 'KK di Perlis' and other non-listed
entries automatically. Run AFTER refine_coords.py to avoid Nominatim overlap.
"""

import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(Path(__file__).resolve().parents[1] / ".env.local")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
sb = create_client(os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
PARENT = "parent_location_id"

OFFICIAL_KK = [
    "Kangar", "Jejawi", "Arau", "Kampung Gial", "Pauh", "Beseri",
    "Padang Besar", "Kaki Bukit", "Simpang Empat", "Kuala Perlis", "Kuala Sanglang",
]


def geocode(place):
    hdr = {"User-Agent": "KKM-Relocation-Planner/1.0"}
    for q in (f"{place}, Perlis, Malaysia", f"Klinik Kesihatan {place}, Perlis, Malaysia"):
        try:
            r = requests.get("https://nominatim.openstreetmap.org/search",
                             params={"q": q, "format": "json", "limit": 1, "countrycodes": "my"},
                             headers=hdr, timeout=30)
            time.sleep(1.1)
            if r.status_code == 200 and r.json():
                h = r.json()[0]
                return round(float(h["lat"]), 6), round(float(h["lon"]), 6)
        except requests.RequestException:
            time.sleep(2)
    return None


def main():
    perlis_jkn = sb.table("locations").select("id").eq("name", "JKN Perlis").eq("tier", "state").limit(1).execute().data[0]["id"]
    pkd_kangar = sb.table("locations").select("id, latitude, longitude").eq("name", "PKD Kangar").eq(PARENT, perlis_jkn).limit(1).execute().data[0]

    clinics = sb.table("locations").select("id, name, metadata").eq("tier", "clinic").eq("address", "Perlis").execute().data or []

    # Delete existing KK rows (category klinik_kesihatan, or uncategorised rows
    # whose name is a health clinic) and the mis-filed dental OFFICE rows.
    del_ids = []
    for r in clinics:
        cat = (r.get("metadata") or {}).get("category")
        name = r["name"]
        low = name.lower()
        is_kk = cat == "klinik_kesihatan" or (not cat and ("klinik kesihatan" in low or low.startswith("kk ")))
        is_office = low.startswith("pejabat kesihatan pergigian daerah")
        if is_kk or is_office:
            del_ids.append(r["id"])
    for i in range(0, len(del_ids), 100):
        sb.table("locations").delete().in_("id", del_ids[i:i + 100]).execute()
    print(f"Deleted {len(del_ids)} old KK / mis-filed office rows.")

    # Insert the 11 official KK, geocoded, parented to PKD Kangar.
    rows, approx = [], 0
    for place in OFFICIAL_KK:
        coord = geocode(place)
        if coord:
            lat, lng, src = coord[0], coord[1], "nominatim_town"
        else:
            lat, lng, src = pkd_kangar["latitude"], pkd_kangar["longitude"], "approx_pkd"
            approx += 1
        rows.append({
            "name": f"Klinik Kesihatan {place}", "tier": "clinic", PARENT: pkd_kangar["id"],
            "latitude": lat, "longitude": lng, "address": "Perlis",
            "metadata": {"kind": "clinic", "category": "klinik_kesihatan", "source": src, "origin": "perlis_directory"},
        })
    sb.table("locations").insert(rows).execute()
    print(f"Inserted {len(rows)} official Perlis KK (approx_coord={approx}).")


if __name__ == "__main__":
    main()
