"""Generic state Klinik Kesihatan reconciler.

Adds missing KK for ONE state from data/klinik_kesihatan_official.json, deduping
against the LIVE DB by normalized name, geocoding by postcode with a town-name
fallback, and parenting to the nearest PKD. Generalizes add_penang_facilities.py.

KK-only: the official JSON has no Klinik Desa (KD).

Run:  python python_scripts/add_state_facilities.py "<state key>" [--dry-run]
  <state key> is a key under "states" in the JSON, e.g. "Johor", "WP Kuala Lumpur".
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

# The WP territories store clinic `address` under more than one spelling; match
# every variant when deduping so we don't re-insert an existing clinic.
ADDRESS_VARIANTS = {
    "WP Kuala Lumpur": ["WP Kuala Lumpur", "Kuala Lumpur"],
    "WP Labuan": ["WP Labuan", "Labuan"],
    "WP Putrajaya": ["WP Putrajaya", "Putrajaya"],
}
# Nominatim-friendly state name for the town-name geocode fallback.
GEO_STATE = {
    "WP Kuala Lumpur": "Kuala Lumpur",
    "WP Labuan": "Labuan",
    "WP Putrajaya": "Putrajaya",
}

# Verified same-facility duplicates the official directory lists WITHOUT the
# locality suffix the DB already uses (confirmed by comparing the official
# address). Name-normalization can't safely catch these without risking
# over-merging distinct "Batu N" clinics, so skip them explicitly.
SKIP = {
    ("Selangor", "KK Batu 9"),  # official addr "Batu 9, Cheras, Hulu Langat" == existing "Klinik Kesihatan Batu 9, Cheras"
}

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")
sb = create_client(os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
PARENT = "parent_location_id"


def norm(name):
    # Canonicalize before comparing so common Malaysian spelling variants and
    # facility-name prefixes don't produce duplicate inserts:
    #   Ayer/Air, Seri/Sri, Sg/Sungei -> Sungai; strip KK / Klinik / KK1M / KD
    #   and the "Klinik Kesihatan"/"Klinik 1 Malaysia" prefixes.
    s = (name or "").lower()
    s = re.sub(r"\bayer\b", "air", s)
    s = re.sub(r"\bseri\b", "sri", s)
    s = re.sub(r"\b(sg|sungei)\b", "sungai", s)
    s = re.sub(r"^(klinik kesihatan ibu dan anak|klinik kesihatan|klinik desa|klinik 1 malaysia|klinik|kk1m|kk|kd)\s+", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def haversine_km(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))


def geocode(name, address, geo_state):
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
                         params={"q": f"{place}, {geo_state}, Malaysia", "format": "json", "limit": 1, "countrycodes": "my"},
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
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    if not args:
        print('Usage: python add_state_facilities.py "<state key>" [--dry-run]')
        sys.exit(1)
    state = args[0]

    jkn_name = "JKN " + state
    geo_state = GEO_STATE.get(state, state)
    variants = ADDRESS_VARIANTS.get(state, [state])
    origin = "moh_" + re.sub(r"[^a-z0-9]+", "_", state.lower()).strip("_") + "_directory"

    data = json.loads((ROOT / "data" / "klinik_kesihatan_official.json").read_text(encoding="utf-8"))
    if state not in data["states"]:
        print(f"No official list for state {state!r}. Keys: {list(data['states'])}")
        sys.exit(1)
    official = data["states"][state]

    jkn_rows = sb.table("locations").select("id, latitude, longitude").eq("name", jkn_name).eq("tier", "state").limit(1).execute().data
    if not jkn_rows:
        print(f"JKN row not found: {jkn_name!r}")
        sys.exit(1)
    jkn = jkn_rows[0]
    pkds = sb.table("locations").select("id, latitude, longitude").eq(PARENT, jkn["id"]).eq("tier", "district").like("name", "PKD %").execute().data or []

    existing = set()
    for variant in variants:
        start = 0
        while True:
            rows = sb.table("locations").select("name").eq("tier", "clinic").eq("address", variant).range(start, start + 999).execute().data
            for r in rows:
                # Dental / lab rows ("Klinik Pergigian...", "Makmal...") are not
                # KKs, so they must not mask a genuinely-missing Klinik Kesihatan.
                if any(x in (r["name"] or "").lower() for x in ("pergigian", "makmal")):
                    continue
                existing.add(norm(r["name"]))
            if len(rows) < 1000:
                break
            start += 1000

    to_add, seen = [], set()
    for it in official:
        name = (it.get("name") or "").replace("&amp;", "&").strip()
        key = norm(name)
        if not key or key in existing or key in seen or (state, name) in SKIP:
            continue
        seen.add(key)
        to_add.append((name, it.get("address", "")))

    print(f"[{state}] official {len(official)} | already in DB {len(official) - len(to_add)} | to add {len(to_add)} | PKDs {len(pkds)}")
    for n, _ in to_add:
        print("   +", n)
    if dry:
        print("   (dry-run: no inserts)")
        return

    approx = 0
    batch = []
    for name, address in to_add:
        g = geocode(name, address, geo_state)
        if g:
            lat, lng, src = g
        else:
            lat, lng, src = jkn["latitude"], jkn["longitude"], "approx_state"
            approx += 1
        parent = min(pkds, key=lambda p: haversine_km(lat, lng, p["latitude"], p["longitude"]))["id"] if pkds else jkn["id"]
        batch.append({
            "name": name, "tier": "clinic", PARENT: parent,
            "latitude": lat, "longitude": lng, "address": state,
            "metadata": {"kind": "clinic", "category": "klinik_kesihatan", "source": src, "origin": origin},
        })
        if len(batch) >= 100:
            sb.table("locations").insert(batch).execute()
            print(f"   inserted {len(batch)}...", flush=True)
            batch = []
    if batch:
        sb.table("locations").insert(batch).execute()

    print(f"[{state}] === DONE === added {len(to_add)} approx_coord={approx}")


if __name__ == "__main__":
    main()
