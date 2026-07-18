"""
Seed the `locations` table with REAL Malaysian public health facilities pulled
from OpenStreetMap (Overpass API). No coordinates are invented.

Hierarchy built (matches the frontend cascade + schema tier constraint):

    JKN <State>        tier = 'state'     (root, one per state/territory)
      |- Hospital X    tier = 'district'  (Box 2)
      |- PKD <Daerah>  tier = 'district'  (Box 2)
           |- KK ...   tier = 'clinic'    (Box 3, parented to nearest PKD)

Box 4 ("Other": MOH HQ / NIH) stays static in the /api/locations route, so it
is intentionally NOT seeded here.

Run:  python python_scripts/seed_facilities.py
"""

import os
import time
import math
from pathlib import Path

import requests
from supabase import create_client


def load_env_file():
    for env_path in [Path(__file__).resolve().parents[1] / ".env.local", Path(__file__).resolve().parents[1] / ".env"]:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value


load_env_file()

SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Please set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in your environment.")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Public Overpass instances, tried in order. maps.mail.ru is fastest for these
# Malaysia queries; overpass-api.de is the fallback. (kumi/private.coffee time out.)
OVERPASS_ENDPOINTS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
PARENT_COLUMN = "parent_location_id"  # confirmed column name for this table

# State/territory display name for the JKN + the exact OSM admin_level=4 name +
# a real coordinate for the state health department (state capital vicinity).
STATES = [
    {"jkn": "JKN Johor", "osm": "Johor", "lat": 1.4927, "lng": 103.7414},
    {"jkn": "JKN Kedah", "osm": "Kedah", "lat": 6.1248, "lng": 100.3678},
    {"jkn": "JKN Kelantan", "osm": "Kelantan", "lat": 6.1254, "lng": 102.2381},
    {"jkn": "JKN Melaka", "osm": "Melaka", "lat": 2.1896, "lng": 102.2501},
    {"jkn": "JKN Negeri Sembilan", "osm": "Negeri Sembilan", "lat": 2.7258, "lng": 101.9424},
    {"jkn": "JKN Pahang", "osm": "Pahang", "lat": 3.8077, "lng": 103.3260},
    {"jkn": "JKN Perak", "osm": "Perak", "lat": 4.5975, "lng": 101.0901},
    {"jkn": "JKN Perlis", "osm": "Perlis", "lat": 6.4414, "lng": 100.1986},
    {"jkn": "JKN Pulau Pinang", "osm": "Pulau Pinang", "lat": 5.4141, "lng": 100.3288},
    {"jkn": "JKN Sabah", "osm": "Sabah", "lat": 5.9804, "lng": 116.0735},
    {"jkn": "JKN Sarawak", "osm": "Sarawak", "lat": 1.5533, "lng": 110.3592},
    {"jkn": "JKN Selangor", "osm": "Selangor", "lat": 3.0733, "lng": 101.5185},
    {"jkn": "JKN Terengganu", "osm": "Terengganu", "lat": 5.3302, "lng": 103.1408},
    {"jkn": "JKN WP Kuala Lumpur", "osm": "Kuala Lumpur", "lat": 3.1390, "lng": 101.6869},
    {"jkn": "JKN WP Putrajaya", "osm": "Putrajaya", "lat": 2.9264, "lng": 101.6964},
    {"jkn": "JKN WP Labuan", "osm": "Labuan", "lat": 5.2831, "lng": 115.2308},
]


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def districts_query(state_osm_name):
    # Lightweight: district (daerah) boundary centers only.
    return f"""
    [out:json][timeout:90];
    area["boundary"="administrative"]["admin_level"="4"]["name"="{state_osm_name}"]->.st;
    relation["boundary"="administrative"]["admin_level"="6"](area.st);
    out center tags;
    """


def facilities_query(state_osm_name):
    # Hospitals + Klinik Kesihatan only (NO admin relations -- those made the
    # combined query time out with a 504).
    return f"""
    [out:json][timeout:90];
    area["boundary"="administrative"]["admin_level"="4"]["name"="{state_osm_name}"]->.st;
    (
      node["amenity"="hospital"]["name"](area.st);
      way["amenity"="hospital"]["name"](area.st);
      node["amenity"~"clinic|doctors"]["name"~"Klinik Kesihatan",i](area.st);
      way["amenity"~"clinic|doctors"]["name"~"Klinik Kesihatan",i](area.st);
      node["healthcare"="clinic"]["name"~"Klinik Kesihatan",i](area.st);
      way["healthcare"="clinic"]["name"~"Klinik Kesihatan",i](area.st);
    );
    out center tags;
    """


def run_overpass(query):
    """POST a query, trying each endpoint with retries. Returns parsed JSON.

    Dense-state facility queries take ~90s, so the request timeout is generous.
    Overpass also sometimes returns HTTP 200 with an empty body and a "remark"
    saying the query timed out -- that soft failure is retried, not accepted.
    """
    headers = {"User-Agent": "KKM-Relocation-Planner/1.0", "Accept": "application/json"}
    last_error = None
    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(3):
            try:
                resp = requests.post(endpoint, data={"data": query}, headers=headers, timeout=160)
                if resp.status_code in (429, 504, 502, 503):
                    last_error = f"HTTP {resp.status_code}"
                    time.sleep(10)
                    continue
                resp.raise_for_status()
                data = resp.json()
                remark = (data.get("remark") or "").lower()
                if "timed out" in remark or "runtime error" in remark:
                    last_error = remark
                    time.sleep(10)
                    continue
                return data
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(8)
    raise RuntimeError(f"Overpass failed on all endpoints: {last_error}")


def _extract(el):
    tags = el.get("tags", {}) or {}
    name = tags.get("name")
    lat, lng = el.get("lat"), el.get("lon")
    if lat is None or lng is None:
        center = el.get("center") or {}
        lat, lng = center.get("lat"), center.get("lon")
    if lat is None or lng is None or not name:
        return None
    return {"name": name, "lat": round(float(lat), 6), "lng": round(float(lng), 6), "tags": tags}


def fetch_state(state_osm_name):
    """Return (districts, hospitals, clinics) lists of {name, lat, lng}."""
    districts, hospitals, clinics = [], [], []

    for el in run_overpass(districts_query(state_osm_name)).get("elements", []):
        if el.get("tags", {}).get("admin_level") != "6":
            continue
        entry = _extract(el)
        if entry:
            districts.append({k: entry[k] for k in ("name", "lat", "lng")})

    time.sleep(2)

    seen = set()
    for el in run_overpass(facilities_query(state_osm_name)).get("elements", []):
        entry = _extract(el)
        if not entry:
            continue
        name, tags = entry["name"], entry["tags"]
        row = {k: entry[k] for k in ("name", "lat", "lng")}
        if tags.get("amenity") == "hospital":
            key = ("h", name)
            if key not in seen:
                seen.add(key)
                hospitals.append(row)
        elif "klinik kesihatan" in name.lower():
            key = ("k", round(entry["lat"], 4), round(entry["lng"], 4))
            if key not in seen:
                seen.add(key)
                clinics.append(row)

    return districts, hospitals, clinics


def insert_rows(rows):
    """Insert a batch and return the created rows (with ids)."""
    if not rows:
        return []
    result = supabase.table("locations").insert(rows).execute()
    return getattr(result, "data", None) or []


def wipe_locations():
    # Delete every row (Supabase requires a filter; this matches all real rows).
    supabase.table("locations").delete().neq("name", "__never__").execute()


def seed():
    print("Wiping existing locations...")
    wipe_locations()

    totals = {"jkn": 0, "pkd": 0, "hospital": 0, "kk": 0}

    for state in STATES:
        print(f"\n=== {state['jkn']} ({state['osm']}) ===")
        try:
            districts, hospitals, clinics = fetch_state(state["osm"])
        except Exception as exc:
            print(f"  ! Overpass error, skipping: {exc}")
            continue

        # 1) JKN root
        jkn = insert_rows([{
            "name": state["jkn"], "tier": "state", PARENT_COLUMN: None,
            "latitude": state["lat"], "longitude": state["lng"],
            "address": state["osm"] + ", Malaysia", "metadata": {"kind": "jkn"},
        }])
        if not jkn:
            print("  ! Failed to insert JKN, skipping state")
            continue
        jkn_id = jkn[0]["id"]
        totals["jkn"] += 1

        # 2) PKD district offices (fallback to one state-wide PKD if none found)
        if not districts:
            districts = [{"name": state["osm"], "lat": state["lat"], "lng": state["lng"]}]
        pkd_rows = [{
            "name": f"PKD {d['name']}", "tier": "district", PARENT_COLUMN: jkn_id,
            "latitude": d["lat"], "longitude": d["lng"],
            "address": f"{d['name']}, {state['osm']}", "metadata": {"kind": "pkd", "district": d["name"]},
        } for d in districts]
        created_pkd = insert_rows(pkd_rows)
        totals["pkd"] += len(created_pkd)
        # Pair created PKD rows back with their district centers for KK matching.
        pkd_points = [
            {"id": row["id"], "lat": d["lat"], "lng": d["lng"]}
            for row, d in zip(created_pkd, districts)
        ]

        # 3) Hospitals (Box 2), parented to the JKN
        hosp_rows = [{
            "name": h["name"], "tier": "district", PARENT_COLUMN: jkn_id,
            "latitude": h["lat"], "longitude": h["lng"],
            "address": state["osm"], "metadata": {"kind": "hospital"},
        } for h in hospitals]
        totals["hospital"] += len(insert_rows(hosp_rows))

        # 4) Klinik Kesihatan (Box 3), parented to the nearest PKD district
        kk_rows = []
        for c in clinics:
            nearest = min(pkd_points, key=lambda p: haversine_km(c["lat"], c["lng"], p["lat"], p["lng"]))
            kk_rows.append({
                "name": c["name"], "tier": "clinic", PARENT_COLUMN: nearest["id"],
                "latitude": c["lat"], "longitude": c["lng"],
                "address": state["osm"], "metadata": {"kind": "kk"},
            })
        # Insert KKs in chunks to stay well within request limits.
        for i in range(0, len(kk_rows), 200):
            totals["kk"] += len(insert_rows(kk_rows[i:i + 200]))

        print(f"  PKD={len(created_pkd)}  hospitals={len(hospitals)}  KK={len(clinics)}")
        time.sleep(3)  # be polite to the public Overpass instance

    print("\n=== DONE ===")
    print(totals)


if __name__ == "__main__":
    seed()
