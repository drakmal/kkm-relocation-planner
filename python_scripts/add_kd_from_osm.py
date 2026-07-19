"""Best-effort Klinik Desa (KD) top-up from OpenStreetMap.

There is NO official KD directory available (the MOH site's directory is gone and
the committed data/klinik_kesihatan_official.json is KK-only), so OSM is the only
broad source for KD. Its coverage is thin -- East Malaysia especially is barely
mapped -- so this ADDS the handful of named KD that OSM has and the DB lacks; it
is not a full reconciliation. KK reconciliation uses the official JSON instead
(add_state_facilities.py).

Filters OSM noise: bare "Klinik Desa" (no locality), "Opposite ..." markers, and
duplicate-mapped nodes (same normalized name within 300 m). Dedupes against the
live DB by normalized name and by any existing clinic row within 600 m. Each new
KD is parented to the nearest PKD, and its state is taken from that PKD's JKN.

Run:  python python_scripts/add_kd_from_osm.py [--dry-run]
"""

import os
import re
import sys
import time
import math

import requests
from dotenv import load_dotenv
from supabase import create_client

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(base, ".env.local"))
load_dotenv(os.path.join(base, ".env"))
sb = create_client(os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
PARENT = "parent_location_id"

# Public Overpass endpoints rotate/rate-limit; try mirrors with backoff.
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def overpass(query):
    last = None
    for attempt in range(6):
        url = OVERPASS_URLS[attempt % len(OVERPASS_URLS)]
        try:
            resp = requests.post(url, data={"data": query}, headers={"User-Agent": "KKM-Relocation-Planner/1.0"}, timeout=180)
            if resp.status_code == 200 and resp.text.lstrip()[:1] == "{":
                return resp.json()
            last = f"{url} -> HTTP {resp.status_code}"
        except requests.RequestException as exc:
            last = f"{url} -> {exc}"
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Overpass failed after retries: {last}")


def norm(s):
    s = (s or "").lower()
    s = re.sub(r"\bayer\b", "air", s)
    s = re.sub(r"\bseri\b", "sri", s)
    s = re.sub(r"\b(sg|sungei)\b", "sungai", s)
    s = re.sub(r"^(klinik kesihatan ibu dan anak|klinik kesihatan|klinik desa|klinik 1 malaysia|klinik|kk1m|kk|kd)\s+", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def hv(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    return 2 * 6371 * math.asin(math.sqrt(math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2))


def fetch_osm_kd():
    q = """
    [out:json][timeout:120];
    area["ISO3166-1"="MY"][admin_level=2]->.my;
    (
      node["name"~"[Kk]linik [Dd]esa"](area.my);
      way["name"~"[Kk]linik [Dd]esa"](area.my);
    );
    out center tags;
    """
    els = overpass(q)["elements"]
    cands = []
    for e in els:
        name = (e.get("tags", {}).get("name") or "").strip()
        if not name.lower().startswith("klinik desa"):
            continue  # drops "Opposite Klinik Desa ..." markers
        if norm(name) == "":
            continue  # bare "Klinik Desa" with no locality
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lng = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None or lng is None:
            continue
        cands.append((name, float(lat), float(lng)))
    return cands


def main():
    dry = "--dry-run" in sys.argv

    cands = fetch_osm_kd()
    print(f"OSM named KD with coords: {len(cands)}")

    # DB clinics (name + coords) for dedup
    rows, start = [], 0
    while True:
        r = sb.table("locations").select("name, latitude, longitude").eq("tier", "clinic").range(start, start + 999).execute().data
        rows += r
        if len(r) < 1000:
            break
        start += 1000
    db = [(norm(x["name"]), x["latitude"], x["longitude"]) for x in rows if x["latitude"] and x["longitude"]]
    dbnames = {n for n, _, _ in db}

    # PKDs (for parenting) + JKN id -> state map (for the address/state)
    jkns = sb.table("locations").select("id, name").eq("tier", "state").execute().data
    state_of = {j["id"]: j["name"].replace("JKN ", "").strip() for j in jkns}
    pkds = sb.table("locations").select("id, name, latitude, longitude, " + PARENT).eq("tier", "district").like("name", "PKD %").execute().data
    pkds = [p for p in pkds if p["latitude"] and p["longitude"]]

    accepted = []
    for name, lat, lng in cands:
        k = norm(name)
        if k in dbnames:
            continue
        if any(hv(lat, lng, dl, dg) < 0.6 for _, dl, dg in db):
            continue  # an existing clinic row sits on this spot already
        if any(k == norm(an) and hv(lat, lng, al, ag) < 0.3 for an, al, ag in accepted):
            continue  # duplicate-mapped OSM node
        accepted.append((name, lat, lng))

    print(f"Genuinely-new KD to add: {len(accepted)}")
    batch = []
    for name, lat, lng in accepted:
        nearest = min(pkds, key=lambda p: hv(lat, lng, p["latitude"], p["longitude"]))
        state = state_of.get(nearest[PARENT], "")
        print(f"  + {name}  ({lat:.5f},{lng:.5f})  -> {nearest['name']} [{state}]")
        batch.append({
            "name": name, "tier": "clinic", PARENT: nearest["id"],
            "latitude": round(lat, 6), "longitude": round(lng, 6), "address": state,
            "metadata": {"kind": "clinic", "category": "klinik_desa", "source": "osm", "origin": "osm_kd_harvest"},
        })

    if dry:
        print("\n--dry-run: no inserts.")
        return
    if batch:
        sb.table("locations").insert(batch).execute()
    print(f"\n=== DONE === added {len(batch)} KD")


if __name__ == "__main__":
    main()
