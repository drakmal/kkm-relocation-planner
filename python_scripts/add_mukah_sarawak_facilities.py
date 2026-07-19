"""Add Bahagian Mukah (Sarawak) hospitals + clinics from a user-supplied list.

Source: data/mukah_sarawak_facilities.xlsx (public info compiled via Google
search). Sarawak is badly under-covered in our OSM-seeded data, and there is no
official statewide KD/KK directory we can reach, so this fills the Mukah
division specifically.

Dedupes against the LIVE DB by a canonicalized name (hospitals vs Sarawak
district-tier rows, clinics vs Sarawak clinic rows), geocodes by postcode with a
town fallback, and inserts:
  - Hospital rows  -> tier 'district', parented to JKN Sarawak
  - Clinic rows    -> tier 'clinic',   parented to the nearest PKD
Category is left to the API's deriveCategory (hospital / klinik_1malaysia /
klinik_kesihatan) but also written to metadata for clarity.

Run:  python python_scripts/add_mukah_sarawak_facilities.py [--dry-run]
"""

import os
import re
import sys
import time
import math

import requests
import openpyxl
from dotenv import load_dotenv
from supabase import create_client

STATE = "Sarawak"
JKN_NAME = "JKN Sarawak"
ORIGIN = "mukah_sarawak_list"

# Verified same-facility duplicates canon can't safely catch (the DB names them
# without the divisional/locality suffix the source list carries).
SKIP = {
    "Klinik Kesihatan Tian, Matu",  # == existing "KK Tian"
}

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
    s = re.sub(r"\bnangga\b", "nanga", s)
    # keep "ibu dan anak" so KKIA is distinct from a plain KK at the same town
    s = re.sub(r"^(klinik kesihatan|klinik desa|klinik 1\s*malaysia|klinik|kk1m|kk|kd)\s+", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


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
    # town = the word(s) right after the 5-digit postcode (usually the district town)
    town = None
    mt = re.search(r"\b\d{5}\s+([A-Za-z][A-Za-z .'-]+?)(?:\s+Sarawak|,|$)", address or "")
    if mt:
        town = mt.group(1).strip()
    if not town:
        town = re.sub(r"^(Hospital|KK|KD|Klinik Kesihatan|Klinik Desa|Klinik 1Malaysia|Klinik)\s+", "", name, flags=re.I).strip()
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q": f"{town}, Sarawak, Malaysia", "format": "json", "limit": 1, "countrycodes": "my"},
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

    wb = openpyxl.load_workbook(os.path.join(ROOT, "data", "mukah_sarawak_facilities.xlsx"), data_only=True)
    ws = wb.active
    facilities = []
    for row in ws.iter_rows(min_row=4, values_only=True):
        if not row[2]:
            continue
        facilities.append({"kat": (row[1] or "").strip(), "name": str(row[2]).strip(), "addr": str(row[3] or "").strip()})

    jkn = sb.table("locations").select("id, latitude, longitude").eq("name", JKN_NAME).eq("tier", "state").limit(1).execute().data[0]
    pkds = [p for p in sb.table("locations").select("id, name, latitude, longitude").eq(PARENT, jkn["id"]).eq("tier", "district").like("name", "PKD %").execute().data if p["latitude"]]

    def existing_set(tier, addr_eq=None):
        out, start = set(), 0
        while True:
            q = sb.table("locations").select("name").eq("tier", tier)
            if addr_eq:
                q = q.eq("address", addr_eq)
            else:
                q = q.eq(PARENT, jkn["id"])
            rows = q.range(start, start + 999).execute().data
            for r in rows:
                out.add(canon(r["name"]))
            if len(rows) < 1000:
                break
            start += 1000
        return out

    existing_clinic = existing_set("clinic", addr_eq=STATE)
    existing_district = existing_set("district")

    to_add = []
    for f in facilities:
        is_hosp = f["kat"].lower() == "hospital"
        if f["name"] in SKIP:
            continue
        c = canon(f["name"])
        pool = existing_district if is_hosp else existing_clinic
        if c in pool:
            continue
        to_add.append((f, is_hosp))

    print(f"Mukah facilities: {len(facilities)} | to add: {len(to_add)}")
    for f, is_hosp in to_add:
        print(f"   + [{'H' if is_hosp else 'C'}] {f['name']}")
    if dry:
        print("\n--dry-run: no inserts.")
        return

    batch, approx = [], 0
    for f, is_hosp in to_add:
        g = geocode(f["name"], f["addr"])
        if g:
            lat, lng, src = g
        else:
            lat, lng, src = jkn["latitude"], jkn["longitude"], "approx_state"
            approx += 1
        low = f["name"].lower()
        if is_hosp:
            tier, parent, category = "district", jkn["id"], "hospital"
            kind = "hospital"
        else:
            tier = "clinic"
            parent = min(pkds, key=lambda p: hv(lat, lng, p["latitude"], p["longitude"]))["id"] if pkds else jkn["id"]
            category = "klinik_1malaysia" if ("1malaysia" in low or "1 malaysia" in low) else "klinik_kesihatan"
            kind = "clinic"
        batch.append({
            "name": f["name"], "tier": tier, PARENT: parent,
            "latitude": lat, "longitude": lng, "address": STATE,
            "metadata": {"kind": kind, "category": category, "source": src, "origin": ORIGIN},
        })

    if batch:
        sb.table("locations").insert(batch).execute()
    print(f"\n=== DONE === added {len(batch)} (approx_coord={approx})")


if __name__ == "__main__":
    main()
