"""Additively add missing Penang (Pulau Pinang) Klinik Kesihatan from the
official MOH directory to the locations table.

Unlike add_perak_facilities.py (which read a scraped .txt), the official list is
already committed at data/klinik_kesihatan_official.json (states -> "Pulau
Pinang"). We dedupe against the LIVE DB by normalized name (not the file's stale
in_osm flag), geocode each new KK by postcode with a town-name fallback, and
parent it to the nearest Penang PKD.

This is the template for reconciling the other 13 unreconciled states: copy it,
change STATE / JKN_NAME / ORIGIN, and (if that state's official list isn't in the
JSON) source it the same way Perak did.

Run:  python python_scripts/add_penang_facilities.py [--dry-run]
"""

import os
import re
import sys
import json
import time
import math
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client

STATE = "Pulau Pinang"
JKN_NAME = "JKN Pulau Pinang"
ORIGIN = "moh_pulau_pinang_directory"

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")
sb = create_client(os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
PARENT = "parent_location_id"


def norm(name):
    s = re.sub(r"^(klinik kesihatan ibu dan anak|klinik kesihatan|klinik desa|kk|kd)\s+", "", (name or "").lower())
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


def haversine_km(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))


def geocode(name, address):
    hdr = {"User-Agent": "KKM-Relocation-Planner/1.0"}
    m = re.search(r"\b(\d{5})\b", address or "")
    if m:
        try:
            r = requests.get("https://nominatim.openstreetmap.org/search",
                             params={"postalcode": m.group(1), "country": "Malaysia", "format": "json", "limit": 1},
                             headers=hdr, timeout=30)
            time.sleep(1.1)
            if r.status_code == 200 and r.json():
                h = r.json()[0]
                return round(float(h["lat"]), 6), round(float(h["lon"]), 6), "nominatim_postcode"
        except requests.RequestException:
            time.sleep(2)
    place = re.sub(r"^(KK|KD|Klinik Kesihatan|Klinik Desa)\s+", "", name, flags=re.I).strip()
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q": f"{place}, {STATE}, Malaysia", "format": "json", "limit": 1, "countrycodes": "my"},
                         headers=hdr, timeout=30)
        time.sleep(1.1)
        if r.status_code == 200 and r.json():
            h = r.json()[0]
            return round(float(h["lat"]), 6), round(float(h["lon"]), 6), "nominatim_town"
    except requests.RequestException:
        time.sleep(2)
    return None


def main():
    dry = "--dry-run" in sys.argv

    data = json.loads((ROOT / "data" / "klinik_kesihatan_official.json").read_text(encoding="utf-8"))
    official = data["states"][STATE]

    jkn = sb.table("locations").select("id, latitude, longitude").eq("name", JKN_NAME).eq("tier", "state").limit(1).execute().data[0]
    pkds = sb.table("locations").select("id, latitude, longitude").eq(PARENT, jkn["id"]).eq("tier", "district").like("name", "PKD %").execute().data or []
    print(f"{JKN_NAME}: {len(pkds)} PKDs for parenting")

    existing, start = set(), 0
    while True:
        rows = sb.table("locations").select("name").eq("tier", "clinic").eq("address", STATE).range(start, start + 999).execute().data
        for r in rows:
            existing.add(norm(r["name"]))
        if len(rows) < 1000:
            break
        start += 1000
    print(f"Existing {STATE} clinic rows: {len(existing)} unique keys")

    to_add, seen = [], set()
    for it in official:
        name = (it.get("name") or "").replace("&amp;", "&").strip()
        key = norm(name)
        if not key or key in existing or key in seen:
            continue
        seen.add(key)
        to_add.append((name, it.get("address", "")))

    print(f"Official {STATE} KK: {len(official)} | already in DB: {len(official) - len(to_add)} | to add: {len(to_add)}")
    for n, _ in to_add:
        print("  +", n)
    if dry:
        print("\n--dry-run: no inserts performed.")
        return

    approx = 0
    batch = []
    for name, address in to_add:
        g = geocode(name, address)
        if g:
            lat, lng, src = g
        else:
            lat, lng, src = jkn["latitude"], jkn["longitude"], "approx_state"
            approx += 1
        parent = min(pkds, key=lambda p: haversine_km(lat, lng, p["latitude"], p["longitude"]))["id"] if pkds else jkn["id"]
        batch.append({
            "name": name, "tier": "clinic", PARENT: parent,
            "latitude": lat, "longitude": lng, "address": STATE,
            "metadata": {"kind": "clinic", "category": "klinik_kesihatan", "source": src, "origin": ORIGIN},
        })
        if len(batch) >= 100:
            sb.table("locations").insert(batch).execute()
            print(f"  inserted {len(batch)}...", flush=True)
            batch = []
    if batch:
        sb.table("locations").insert(batch).execute()

    print(f"\n=== DONE ===  added {len(to_add)}  approx_coord={approx}")


if __name__ == "__main__":
    main()
