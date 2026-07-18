"""
Repair pass: re-fetch hospitals + Klinik Kesihatan for states whose facilities
query came back empty during the main seed, and attach them to the JKN / PKD
rows that already exist. Does NOT touch other states or the districts.

Run:  python python_scripts/repair_facilities.py
"""

import time

from seed_facilities import (
    supabase, PARENT_COLUMN, haversine_km,
    run_overpass, facilities_query, insert_rows,
)

# (JKN display name, OSM admin_level=4 name) pairs that need repairing.
STATES_TO_FIX = [
    ("JKN Johor", "Johor"),
    ("JKN Perlis", "Perlis"),
    ("JKN Pulau Pinang", "Pulau Pinang"),
    ("JKN Selangor", "Selangor"),
]


def _extract(el):
    tags = el.get("tags", {}) or {}
    name = tags.get("name")
    lat, lng = el.get("lat"), el.get("lon")
    if lat is None or lng is None:
        center = el.get("center") or {}
        lat, lng = center.get("lat"), center.get("lon")
    if lat is None or lng is None or not name:
        return None
    return {"name": name, "lat": round(float(lat), 6), "lng": round(float(lng), 6), "amenity": tags.get("amenity")}


def repair():
    totals = {"hospital": 0, "kk": 0}

    for jkn_name, osm_name in STATES_TO_FIX:
        print(f"\n=== {jkn_name} ({osm_name}) ===", flush=True)

        jkn = supabase.table("locations").select("id").eq("name", jkn_name).eq("tier", "state").limit(1).execute()
        if not jkn.data:
            print("  ! JKN row not found, skipping", flush=True)
            continue
        jkn_id = jkn.data[0]["id"]

        # Existing PKD district offices for this state (for KK parenting).
        pkd = (
            supabase.table("locations")
            .select("id, latitude, longitude")
            .eq(PARENT_COLUMN, jkn_id).eq("tier", "district")
            .like("name", "PKD %").execute()
        )
        pkd_points = [{"id": r["id"], "lat": r["latitude"], "lng": r["longitude"]} for r in (pkd.data or [])]
        if not pkd_points:
            print("  ! no PKD rows found, skipping", flush=True)
            continue

        try:
            data = run_overpass(facilities_query(osm_name))
        except Exception as exc:
            print(f"  ! Overpass failed: {exc}", flush=True)
            continue

        hospitals, clinics, seen = [], [], set()
        for el in data.get("elements", []):
            e = _extract(el)
            if not e:
                continue
            if e["amenity"] == "hospital":
                if ("h", e["name"]) in seen:
                    continue
                seen.add(("h", e["name"]))
                hospitals.append(e)
            elif "klinik kesihatan" in e["name"].lower():
                k = ("k", round(e["lat"], 4), round(e["lng"], 4))
                if k in seen:
                    continue
                seen.add(k)
                clinics.append(e)

        hosp_rows = [{
            "name": h["name"], "tier": "district", PARENT_COLUMN: jkn_id,
            "latitude": h["lat"], "longitude": h["lng"],
            "address": osm_name, "metadata": {"kind": "hospital"},
        } for h in hospitals]
        totals["hospital"] += len(insert_rows(hosp_rows))

        kk_rows = []
        for c in clinics:
            nearest = min(pkd_points, key=lambda p: haversine_km(c["lat"], c["lng"], p["lat"], p["lng"]))
            kk_rows.append({
                "name": c["name"], "tier": "clinic", PARENT_COLUMN: nearest["id"],
                "latitude": c["lat"], "longitude": c["lng"],
                "address": osm_name, "metadata": {"kind": "kk"},
            })
        for i in range(0, len(kk_rows), 200):
            totals["kk"] += len(insert_rows(kk_rows[i:i + 200]))

        print(f"  hospitals={len(hospitals)}  KK={len(clinics)}", flush=True)
        time.sleep(2)

    print("\n=== REPAIR DONE ===", flush=True)
    print(totals, flush=True)


if __name__ == "__main__":
    repair()
