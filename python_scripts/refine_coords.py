"""
Refine clinic rows that were seeded with approximate (state-centre) coordinates
(metadata.source == 'approx_state'). Re-geocode them via OSM Nominatim using the
town/place extracted from the facility name, and re-parent to the nearest PKD
(health clinics / Klinik Desa) or PKPD (dental clinics).

Run:  python python_scripts/refine_coords.py
"""

import os
import re
import time
import math
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(Path(__file__).resolve().parents[1] / ".env.local")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
sb = create_client(os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
PARENT = "parent_location_id"

STATE_TO_JKN = {
    "Johor": "JKN Johor", "Kedah": "JKN Kedah", "Kelantan": "JKN Kelantan", "Melaka": "JKN Melaka",
    "Negeri Sembilan": "JKN Negeri Sembilan", "Pahang": "JKN Pahang", "Pulau Pinang": "JKN Pulau Pinang",
    "Perak": "JKN Perak", "Perlis": "JKN Perlis", "Sabah": "JKN Sabah", "Sarawak": "JKN Sarawak",
    "Selangor": "JKN Selangor", "Terengganu": "JKN Terengganu", "WP Kuala Lumpur": "JKN WP Kuala Lumpur",
    "WP Labuan": "JKN WP Labuan", "WP Putrajaya": "JKN WP Putrajaya",
}


def haversine_km(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))


def place_from_name(name):
    s = re.sub(r"^Klinik Pergigian di\s+", "", name, flags=re.I)
    s = re.sub(r"^(Klinik Kesihatan Ibu dan Anak|Klinik Kesihatan|Hospital|Klinik Desa|Klinik|KKIA|KK|KD|KP)\s+", "", s, flags=re.I)
    return s.strip()


def geocode(name, state):
    place = place_from_name(name)
    hdr = {"User-Agent": "KKM-Relocation-Planner/1.0"}
    for q in (f"{place}, {state}, Malaysia", f"Klinik Kesihatan {place}, {state}, Malaysia"):
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


def office_points():
    """Per-JKN PKD and PKPD points, for re-parenting."""
    jkns = {r["name"]: r["id"] for r in (sb.table("locations").select("id, name").eq("tier", "state").execute().data or [])}
    pkd, pkpd = {}, {}
    for jname, jid in jkns.items():
        rows = sb.table("locations").select("id, latitude, longitude, name").eq(PARENT, jid).eq("tier", "district").execute().data or []
        pkd[jname] = [{"id": r["id"], "lat": r["latitude"], "lng": r["longitude"]} for r in rows if r["name"].lower().startswith("pkd ")]
        pkpd[jname] = [{"id": r["id"], "lat": r["latitude"], "lng": r["longitude"]} for r in rows if r["name"].lower().startswith("pkpd ")]
    return pkd, pkpd


def fetch_approx_rows():
    rows, start = [], 0
    while True:
        batch = sb.table("locations").select("id, name, address, metadata").eq("tier", "clinic").range(start, start + 999).execute().data
        for r in batch:
            m = r.get("metadata") or {}
            if isinstance(m, dict) and "approx" in (m.get("source") or ""):
                rows.append(r)
        if len(batch) < 1000:
            break
        start += 1000
    return rows


def main():
    pkd, pkpd = office_points()
    rows = fetch_approx_rows()
    print(f"{len(rows)} approx-coord rows to refine.", flush=True)

    rescued = 0
    for r in rows:
        state = r.get("address") or ""
        jname = STATE_TO_JKN.get(state)
        coord = geocode(r["name"], state)
        if not coord:
            continue
        lat, lng = coord
        cat = (r.get("metadata") or {}).get("category")
        points = (pkpd if cat == "klinik_pergigian" else pkd).get(jname) or []
        update = {
            "latitude": lat, "longitude": lng,
            "metadata": {**(r.get("metadata") or {}), "source": "nominatim_refined"},
        }
        if points:
            update[PARENT] = min(points, key=lambda p: haversine_km(lat, lng, p["lat"], p["lng"]))["id"]
        sb.table("locations").update(update).eq("id", r["id"]).execute()
        rescued += 1
        if rescued % 50 == 0:
            print(f"  rescued {rescued}...", flush=True)

    print(f"\n=== DONE ===  refined {rescued} of {len(rows)} approx rows.", flush=True)


if __name__ == "__main__":
    main()
