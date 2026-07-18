"""
Geocode and seed the Klinik Kesihatan that the official MOH directory lists but
OpenStreetMap was missing (data/klinik_kesihatan_missing.json). Each is geocoded
via OSM Nominatim from its official full address and inserted as a clinic
(category='klinik_kesihatan') parented to the nearest PKD.

Idempotent: skips any KK name already present in the clinic tier.

Run:  python python_scripts/seed_missing_kk.py
"""

import os
import re
import json
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
MISSING = Path(__file__).resolve().parents[1] / "data" / "klinik_kesihatan_missing.json"

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


def place_of(name):
    return re.sub(r"^(KK|Klinik Kesihatan Ibu dan Anak|Klinik Kesihatan|KKIA)\s+", "", name, flags=re.I).strip()


def district_of(address):
    parts = [p.strip() for p in (address or "").split(",")]
    return parts[-2] if len(parts) >= 2 else ""


def geocode(name, address, state):
    # Town-based query resolves best (the clinic sits in that town); fall back
    # to the KK name, then the full official address.
    place = place_of(name)
    district = district_of(address)
    queries = [
        f"{place}, {district}, {state}, Malaysia" if district else None,
        f"Klinik Kesihatan {place}, {state}, Malaysia",
        address,
    ]
    hdr = {"User-Agent": "KKM-Relocation-Planner/1.0"}
    for q in queries:
        if not q:
            continue
        try:
            r = requests.get("https://nominatim.openstreetmap.org/search",
                             params={"q": q, "format": "json", "limit": 1, "countrycodes": "my"},
                             headers=hdr, timeout=30)
            time.sleep(1.1)
            if r.status_code == 200 and r.json():
                h = r.json()[0]
                return round(float(h["lat"]), 6), round(float(h["lon"]), 6), "nominatim_town"
        except requests.RequestException:
            time.sleep(2)
    return None


def jkn_and_pkd():
    jkns = {r["name"]: r for r in (sb.table("locations").select("*").eq("tier", "state").execute().data or [])}
    pkd = {}
    for jname, j in jkns.items():
        rows = sb.table("locations").select("id, latitude, longitude").eq(PARENT, j["id"]).eq("tier", "district").like("name", "PKD %").execute().data or []
        pkd[jname] = [{"id": r["id"], "lat": r["latitude"], "lng": r["longitude"]} for r in rows]
    return jkns, pkd


def existing_clinic_names():
    names, start = set(), 0
    while True:
        rows = sb.table("locations").select("name").eq("tier", "clinic").range(start, start + 999).execute().data
        for r in rows:
            names.add(r["name"].lower())
        if len(rows) < 1000:
            break
        start += 1000
    return names


def main():
    payload = json.loads(MISSING.read_text(encoding="utf-8"))
    states = payload["states"]
    jkns, pkd = jkn_and_pkd()
    have = existing_clinic_names()

    inserted = approx = skipped = 0
    for state, items in states.items():
        jname = STATE_TO_JKN.get(state)
        j = jkns.get(jname)
        if not j:
            print(f"! {state}: no JKN, skip", flush=True)
            continue
        pts = pkd.get(jname) or []
        rows = []
        for it in items:
            name, addr = it["name"], it.get("address", "")
            if name.lower() in have:
                skipped += 1
                continue
            g = geocode(name, addr, state)
            if g:
                lat, lng, src = g
            else:
                lat, lng, src = j["latitude"], j["longitude"], "approx_state"
                approx += 1
            parent = min(pts, key=lambda p: haversine_km(lat, lng, p["lat"], p["lng"]))["id"] if pts else j["id"]
            rows.append({
                "name": name, "tier": "clinic", PARENT: parent,
                "latitude": lat, "longitude": lng, "address": state,
                "metadata": {"kind": "clinic", "category": "klinik_kesihatan", "source": src, "origin": "moh_directory"},
            })
            have.add(name.lower())
        for i in range(0, len(rows), 200):
            sb.table("locations").insert(rows[i:i + 200]).execute()
        inserted += len(rows)
        print(f"  {state}: +{len(rows)} KK", flush=True)

    print(f"\n=== DONE ===  inserted={inserted}  approx_coord={approx}  skipped_existing={skipped}", flush=True)


if __name__ == "__main__":
    main()
