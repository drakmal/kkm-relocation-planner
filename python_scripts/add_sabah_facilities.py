"""Add Sabah hospitals + clinics from the Sabah SKN facility directory.

Source: data/sabah_health_facilities.json, scraped from
https://skn.sabah.gov.my/iknow/kesihatan-keselamatan/fasiliti-kesihatan
(the state health directory — richer than the moh.gov.my KK list, and the only
source with Sabah's Klinik Desa, which moh.gov.my omits).

Categorization is by the facility NAME prefix, not the source's "type" column
(which labels most rows "Klinik Kesihatan" even when the name is "KD ..."):
  Hospital ... -> tier district (parent JKN Sabah)
  KD ... / Klinik Desa ...        -> clinic, klinik_desa
  KKom ... / Klinik Komuniti ...  -> clinic, klinik_kesihatan (community clinic)
  KK ... / Klinik Kesihatan ...   -> clinic, klinik_kesihatan

Dedup is CATEGORY-AWARE (a source "KD X" only dedupes against existing KD-type
rows, "KK X" against KK-type) so a KK and KD at the same locality aren't merged.
Parents each clinic to "PKD <Daerah>" from the source's district column, falling
back to the nearest PKD. Geocodes by postcode with a town fallback.

Run:  python python_scripts/add_sabah_facilities.py [--dry-run]
"""

import os
import re
import sys
import time
import math
import json

import requests
from dotenv import load_dotenv
from supabase import create_client

STATE = "Sabah"
JKN_NAME = "JKN Sabah"
ORIGIN = "sabah_skn_directory"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env.local"))
load_dotenv(os.path.join(ROOT, ".env"))
sb = create_client(os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
PARENT = "parent_location_id"


def canon(s):
    s = (s or "").lower()
    s = re.sub(r"\bayer\b", "air", s)
    s = re.sub(r"\bseri\b", "sri", s)
    s = re.sub(r"\b(sg|sungei)\b", "sungai", s)
    s = re.sub(r"\bkpg\b", "kampung", s)
    s = re.sub(r"^(klinik kesihatan ibu dan anak|klinik kesihatan|klinik komuniti|klinik desa|klinik 1\s*malaysia|klinik|kkom|kk1m|kk|kd)\s+", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def is_kd(name):
    n = (name or "").lower()
    return n.startswith("kd ") or "klinik desa" in n


def hv(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    return 2 * 6371 * math.asin(math.sqrt(math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2))


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
    place = re.sub(r"^(Hospital|KKom|KK|KD|Klinik Kesihatan|Klinik Komuniti|Klinik Desa|Klinik)\s+", "", name, flags=re.I).strip()
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q": f"{place}, Sabah, Malaysia", "format": "json", "limit": 1, "countrycodes": "my"},
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

    facilities = json.loads(open(os.path.join(ROOT, "data", "sabah_health_facilities.json"), encoding="utf-8").read())

    jkn = sb.table("locations").select("id, latitude, longitude").eq("name", JKN_NAME).eq("tier", "state").limit(1).execute().data[0]
    pkds = [p for p in sb.table("locations").select("id, name, latitude, longitude").eq(PARENT, jkn["id"]).eq("tier", "district").like("name", "PKD %").execute().data if p["latitude"]]
    pkd_by_district = {p["name"].replace("PKD ", "").strip().lower(): p for p in pkds}

    # existing Sabah rows, split into category buckets for category-aware dedup
    existing_district, existing_kk, existing_kd = set(), set(), set()
    for d in sb.table("locations").select("name").eq("tier", "district").eq(PARENT, jkn["id"]).execute().data:
        existing_district.add(canon(d["name"]))
    start = 0
    while True:
        rows = sb.table("locations").select("name, metadata").eq("tier", "clinic").eq("address", STATE).range(start, start + 999).execute().data
        for r in rows:
            cat = (r.get("metadata") or {}).get("category")
            (existing_kd if (is_kd(r["name"]) or cat == "klinik_desa") else existing_kk).add(canon(r["name"]))
        if len(rows) < 1000:
            break
        start += 1000
    print(f"Existing Sabah -> districts:{len(existing_district)} KK:{len(existing_kk)} KD:{len(existing_kd)}")

    to_add, seen = [], set()
    for f in facilities:
        name = f["name"].strip()
        is_hosp = name.lower().startswith("hospital")
        kd = is_kd(name)
        c = canon(name)
        bucket = "H" if is_hosp else ("KD" if kd else "KK")
        pool = existing_district if is_hosp else (existing_kd if kd else existing_kk)
        if not c or c in pool or (bucket, c) in seen:
            continue
        seen.add((bucket, c))
        to_add.append(f)

    n_h = sum(1 for f in to_add if f["name"].lower().startswith("hospital"))
    n_kd = sum(1 for f in to_add if is_kd(f["name"]) and not f["name"].lower().startswith("hospital"))
    n_kk = len(to_add) - n_h - n_kd
    print(f"To add: {len(to_add)}  (Hospital {n_h}, KD {n_kd}, KK/KKom {n_kk})")
    if dry:
        for f in to_add[:400]:
            print(f"   + [{f['type'][:4]}] {f['name']}  ({f['district']})")
        print("\n--dry-run: no inserts.")
        return

    batch, approx = [], 0
    for f in to_add:
        name = f["name"].strip()
        is_hosp = name.lower().startswith("hospital")
        g = geocode(name, f.get("address", ""))
        if g:
            lat, lng, src = g
        else:
            lat, lng, src = jkn["latitude"], jkn["longitude"], "approx_state"
            approx += 1
        if is_hosp:
            tier, parent, category, kind = "district", jkn["id"], "hospital", "hospital"
        else:
            tier, kind = "clinic", "clinic"
            category = "klinik_desa" if is_kd(name) else "klinik_kesihatan"
            pk = pkd_by_district.get((f.get("district") or "").strip().lower())
            if not pk:
                pk = min(pkds, key=lambda p: hv(lat, lng, p["latitude"], p["longitude"])) if pkds else None
            parent = pk["id"] if pk else jkn["id"]
        batch.append({
            "name": name, "tier": tier, PARENT: parent,
            "latitude": lat, "longitude": lng, "address": STATE,
            "metadata": {"kind": kind, "category": category, "source": src, "origin": ORIGIN},
        })
        if len(batch) >= 100:
            sb.table("locations").insert(batch).execute()
            print(f"   inserted {len(batch)}...", flush=True)
            batch = []
    if batch:
        sb.table("locations").insert(batch).execute()
    print(f"\n=== DONE === added {len(to_add)} (approx_coord={approx})")


if __name__ == "__main__":
    main()
