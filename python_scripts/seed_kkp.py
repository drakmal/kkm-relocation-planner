"""
Seed KKP (Klinik Pergigian = government dental clinics) from the official MOH
dental-clinic directory (scraped to a browser tool-results dump), geocoded via
OpenStreetMap Nominatim from each clinic's official full address.

tier='clinic', metadata.category='klinik_pergigian', parented to the nearest
PKPD (district dental office). Idempotent: skips names already stored as KKP.

Run:  python python_scripts/seed_kkp.py "<tool-results .txt path>"
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

STATE_TO_JKN = {
    "Johor": "JKN Johor", "Kedah": "JKN Kedah", "Kelantan": "JKN Kelantan", "Melaka": "JKN Melaka",
    "Negeri Sembilan": "JKN Negeri Sembilan", "Pahang": "JKN Pahang", "Pulau Pinang": "JKN Pulau Pinang",
    "Perak": "JKN Perak", "Perlis": "JKN Perlis", "Sabah": "JKN Sabah", "Sarawak": "JKN Sarawak",
    "Selangor": "JKN Selangor", "Terengganu": "JKN Terengganu", "WP Kuala Lumpur": "JKN WP Kuala Lumpur",
    "WP Labuan": "JKN WP Labuan", "WP Putrajaya": "JKN WP Putrajaya",
}


def load_dump(path):
    raw = Path(path).read_text(encoding="utf-8")
    dec = json.JSONDecoder()
    env = json.loads(raw)
    val = env[0]["text"] if isinstance(env, list) else raw
    for _ in range(3):
        if isinstance(val, dict):
            return val
        val, _ = dec.raw_decode(val.lstrip())
    raise ValueError("cannot decode")


def haversine_km(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))


def geocode(address, name, state):
    hdr = {"User-Agent": "KKM-Relocation-Planner/1.0"}
    for q in (address, f"{name}, {state}, Malaysia"):
        if not q:
            continue
        try:
            r = requests.get("https://nominatim.openstreetmap.org/search",
                             params={"q": q, "format": "json", "limit": 1, "countrycodes": "my"},
                             headers=hdr, timeout=30)
            time.sleep(1.1)
            if r.status_code == 200 and r.json():
                h = r.json()[0]
                return round(float(h["lat"]), 6), round(float(h["lon"]), 6), "nominatim"
        except requests.RequestException:
            time.sleep(2)
    return None


def jkn_and_pkpd():
    jkns = {r["name"]: r for r in (sb.table("locations").select("*").eq("tier", "state").execute().data or [])}
    pkpd = {}
    for jname, j in jkns.items():
        rows = sb.table("locations").select("id, latitude, longitude").eq(PARENT, j["id"]).eq("tier", "district").like("name", "PKPD %").execute().data or []
        pkpd[jname] = [{"id": r["id"], "lat": r["latitude"], "lng": r["longitude"]} for r in rows]
    return jkns, pkpd


def existing_kkp_names():
    names, start = set(), 0
    while True:
        rows = sb.table("locations").select("name, metadata").eq("tier", "clinic").range(start, start + 999).execute().data
        for r in rows:
            if isinstance(r.get("metadata"), dict) and r["metadata"].get("category") == "klinik_pergigian":
                names.add(r["name"].lower())
        if len(rows) < 1000:
            break
        start += 1000
    return names


def main():
    data = load_dump(sys.argv[1])
    jkns, pkpd = jkn_and_pkpd()
    have = existing_kkp_names()

    inserted = approx = 0
    for state, items in data.items():
        jname = STATE_TO_JKN.get(state)
        j = jkns.get(jname)
        if not j:
            print(f"! {state}: no JKN, skip", flush=True)
            continue
        pts = pkpd.get(jname) or []
        rows = []
        for it in items:
            name, addr = it["n"], it.get("a", "")
            if name.lower() in have:
                continue
            g = geocode(addr, name, state)
            if g:
                lat, lng, src = g
            else:
                lat, lng, src = j["latitude"], j["longitude"], "approx_state"
                approx += 1
            parent = min(pts, key=lambda p: haversine_km(lat, lng, p["lat"], p["lng"]))["id"] if pts else j["id"]
            rows.append({
                "name": name, "tier": "clinic", PARENT: parent,
                "latitude": lat, "longitude": lng, "address": state,
                "metadata": {"kind": "clinic", "category": "klinik_pergigian", "source": src},
            })
            have.add(name.lower())
        for i in range(0, len(rows), 200):
            sb.table("locations").insert(rows[i:i + 200]).execute()
        inserted += len(rows)
        print(f"  {state}: +{len(rows)} KKP", flush=True)

    print(f"\n=== DONE ===  inserted={inserted}  approx_coord={approx}", flush=True)


if __name__ == "__main__":
    main()
