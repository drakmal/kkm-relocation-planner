"""
Additively update Perak facilities from the official MOH health-clinic directory
(moh.gov.my/.../klinik-kesihatan/perak). Adds any KK / KD listed there that is
NOT already in the DB. Existing rows are left untouched.

Geocodes each new facility by postcode (town-level) with a place-name fallback,
and parents it to the nearest PKD.

Run:  python python_scripts/add_perak_facilities.py "<perak tool-results .txt>"
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

load_dotenv(Path(__file__).resolve().parents[1] / ".env.local")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
sb = create_client(os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
PARENT = "parent_location_id"


def load_official(path):
    dec = json.JSONDecoder()
    env = json.loads(Path(path).read_text(encoding="utf-8"))
    val = env[0]["text"] if isinstance(env, list) else env
    for _ in range(4):
        if isinstance(val, list):
            return val
        val, _ = dec.raw_decode(val.lstrip())  # peels layers, ignores padding
    raise ValueError("could not decode Perak list")


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
                         params={"q": f"{place}, Perak, Malaysia", "format": "json", "limit": 1, "countrycodes": "my"},
                         headers=hdr, timeout=30)
        time.sleep(1.1)
        if r.status_code == 200 and r.json():
            h = r.json()[0]
            return round(float(h["lat"]), 6), round(float(h["lon"]), 6), "nominatim_town"
    except requests.RequestException:
        time.sleep(2)
    return None


def main():
    official = load_official(sys.argv[1])
    print(f"Official Perak facilities: {len(official)}")

    jkn = sb.table("locations").select("id, latitude, longitude").eq("name", "JKN Perak").eq("tier", "state").limit(1).execute().data[0]
    pkds = sb.table("locations").select("id, latitude, longitude").eq(PARENT, jkn["id"]).eq("tier", "district").like("name", "PKD %").execute().data or []

    # Existing Perak clinic names (normalised) to avoid duplicates.
    existing, start = set(), 0
    while True:
        rows = sb.table("locations").select("name").eq("tier", "clinic").eq("address", "Perak").range(start, start + 999).execute().data
        for r in rows:
            existing.add(norm(r["name"]))
        if len(rows) < 1000:
            break
        start += 1000
    print(f"Existing Perak clinic rows: {len(existing)} unique keys")

    to_add = []
    seen = set()
    for it in official:
        name = (it.get("n") or "").replace("&amp;", "&").strip()
        key = norm(name)
        if not key or key in existing or key in seen:
            continue
        seen.add(key)
        to_add.append((name, it.get("a", "")))
    print(f"Missing (to add): {len(to_add)}")

    added = {"klinik_desa": 0, "klinik_kesihatan": 0}
    approx = 0
    batch = []
    for name, address in to_add:
        cat = "klinik_desa" if re.match(r"^KD\b", name, re.I) else "klinik_kesihatan"
        g = geocode(name, address)
        if g:
            lat, lng, src = g
        else:
            lat, lng, src = jkn["latitude"], jkn["longitude"], "approx_state"
            approx += 1
        parent = min(pkds, key=lambda p: haversine_km(lat, lng, p["latitude"], p["longitude"]))["id"] if pkds else jkn["id"]
        batch.append({
            "name": name, "tier": "clinic", PARENT: parent,
            "latitude": lat, "longitude": lng, "address": "Perak",
            "metadata": {"kind": "clinic", "category": cat, "source": src, "origin": "moh_perak_directory"},
        })
        added[cat] += 1
        if len(batch) >= 100:
            sb.table("locations").insert(batch).execute()
            print(f"  inserted {len(batch)}...", flush=True)
            batch = []
    if batch:
        sb.table("locations").insert(batch).execute()

    print(f"\n=== DONE ===  added {added}  approx_coord={approx}")


if __name__ == "__main__":
    main()
