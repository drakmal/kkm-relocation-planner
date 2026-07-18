"""
Second-pass refinement for the clinic rows still on approximate (state-centre)
coordinates. Uses each facility's official full address (from the KK/KKP source
files) to geocode by POSTCODE via Nominatim -- Malaysian postcodes resolve to a
town-level area, far better than a state centre. Falls back to a place-name
query. Re-parents to the nearest PKD (health/desa) or PKPD (dental).

Run:  python python_scripts/refine_coords_v2.py "<dental tool-results .txt>"
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
ROOT = Path(__file__).resolve().parents[1]

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


def load_addresses(dental_path):
    """name(lower) -> official full address, from the KK missing file + dental dump."""
    amap = {}
    kk = json.loads((ROOT / "data" / "klinik_kesihatan_missing.json").read_text(encoding="utf-8"))
    for items in kk["states"].values():
        for it in items:
            amap[it["name"].lower()] = it.get("address", "")
    if dental_path and Path(dental_path).exists():
        dec = json.JSONDecoder()
        val = json.loads(Path(dental_path).read_text(encoding="utf-8"))[0]["text"]
        for _ in range(3):
            if isinstance(val, dict):
                break
            val, _ = dec.raw_decode(val.lstrip())
        for items in val.values():
            for it in items:
                amap[it["n"].lower()] = it.get("a", "")
    return amap


def place_from_name(name):
    s = re.sub(r"^Klinik Pergigian di\s+", "", name, flags=re.I)
    s = re.sub(r"^(Klinik Kesihatan Ibu dan Anak|Klinik Kesihatan|Hospital|Klinik Desa|Klinik|KKIA|KK|KD|KP)\s+", "", s, flags=re.I)
    return s.strip()


def geocode(name, state, address):
    hdr = {"User-Agent": "KKM-Relocation-Planner/1.0"}
    postcode = None
    m = re.search(r"\b(\d{5})\b", address or "")
    if m:
        postcode = m.group(1)
    # 1) postcode (town-level), 2) place name
    if postcode:
        try:
            r = requests.get("https://nominatim.openstreetmap.org/search",
                             params={"postalcode": postcode, "country": "Malaysia", "format": "json", "limit": 1},
                             headers=hdr, timeout=30)
            time.sleep(1.1)
            if r.status_code == 200 and r.json():
                h = r.json()[0]
                return round(float(h["lat"]), 6), round(float(h["lon"]), 6), "nominatim_postcode"
        except requests.RequestException:
            time.sleep(2)
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q": f"{place_from_name(name)}, {state}, Malaysia", "format": "json", "limit": 1, "countrycodes": "my"},
                         headers=hdr, timeout=30)
        time.sleep(1.1)
        if r.status_code == 200 and r.json():
            h = r.json()[0]
            return round(float(h["lat"]), 6), round(float(h["lon"]), 6), "nominatim_town"
    except requests.RequestException:
        time.sleep(2)
    return None


def office_points():
    jkns = {r["name"]: r["id"] for r in (sb.table("locations").select("id, name").eq("tier", "state").execute().data or [])}
    pkd, pkpd = {}, {}
    for jname, jid in jkns.items():
        rows = sb.table("locations").select("id, latitude, longitude, name").eq(PARENT, jid).eq("tier", "district").execute().data or []
        pkd[jname] = [{"id": r["id"], "lat": r["latitude"], "lng": r["longitude"]} for r in rows if r["name"].lower().startswith("pkd ")]
        pkpd[jname] = [{"id": r["id"], "lat": r["latitude"], "lng": r["longitude"]} for r in rows if r["name"].lower().startswith("pkpd ")]
    return pkd, pkpd


def main():
    dental_path = sys.argv[1] if len(sys.argv) > 1 else None
    amap = load_addresses(dental_path)
    pkd, pkpd = office_points()

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
    print(f"{len(rows)} still-approx rows.", flush=True)

    rescued = 0
    for r in rows:
        state = r.get("address") or ""
        jname = STATE_TO_JKN.get(state)
        address = amap.get(r["name"].lower(), "")
        g = geocode(r["name"], state, address)
        if not g:
            continue
        lat, lng, src = g
        cat = (r.get("metadata") or {}).get("category")
        points = (pkpd if cat == "klinik_pergigian" else pkd).get(jname) or []
        upd = {"latitude": lat, "longitude": lng, "metadata": {**(r.get("metadata") or {}), "source": src}}
        if points:
            upd[PARENT] = min(points, key=lambda p: haversine_km(lat, lng, p["lat"], p["lng"]))["id"]
        sb.table("locations").update(upd).eq("id", r["id"]).execute()
        rescued += 1
        if rescued % 40 == 0:
            print(f"  rescued {rescued}...", flush=True)

    print(f"\n=== DONE ===  refined {rescued} of {len(rows)} (postcode/place pass).", flush=True)


if __name__ == "__main__":
    main()
