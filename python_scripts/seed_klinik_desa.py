"""
Enrich Box 3 with REAL Klinik Desa (rural clinics) and Klinik 1Malaysia from
OpenStreetMap -- government primary-care facilities tagged operator=Ministry of
Health that the name-only "Klinik Kesihatan" filter missed.

ADDITIVE: existing clinic-tier rows are kept; only genuinely new facilities
(deduped by coordinate against what's already stored) are inserted. Each new row
is tagged with metadata.category so Klinik Desa vs Klinik Kesihatan is explicit.

Run:  python python_scripts/seed_klinik_desa.py
"""

import time

from seed_facilities import (
    supabase, PARENT_COLUMN, STATES, haversine_km, run_overpass,
)


def query(state_osm):
    return f"""
    [out:json][timeout:120];
    area["boundary"="administrative"]["admin_level"="4"]["name"="{state_osm}"]->.st;
    (
      node["amenity"~"clinic|doctors"]["operator"~"Kementerian Kesihatan|Ministry of Health|Kesihatan Malaysia|KKM",i](area.st);
      way["amenity"~"clinic|doctors"]["operator"~"Kementerian Kesihatan|Ministry of Health|Kesihatan Malaysia|KKM",i](area.st);
      node["healthcare"]["operator"~"Kementerian Kesihatan|Ministry of Health|KKM",i](area.st);
      node["amenity"~"clinic|doctors"]["name"~"Klinik Desa|Klinik 1Malaysia",i](area.st);
      way["amenity"~"clinic|doctors"]["name"~"Klinik Desa|Klinik 1Malaysia",i](area.st);
      node["healthcare"]["name"~"Klinik Desa|Klinik 1Malaysia",i](area.st);
    );
    out center tags;
    """


def classify(name):
    low = name.lower()
    if "klinik desa" in low:
        return "klinik_desa"
    if "klinik kesihatan" in low:
        return "klinik_kesihatan"
    if "1malaysia" in low or "1 malaysia" in low:
        return "klinik_1malaysia"
    return "government_clinic"


def extract(el):
    tags = el.get("tags", {}) or {}
    name = tags.get("name")
    lat, lng = el.get("lat"), el.get("lon")
    if lat is None or lng is None:
        c = el.get("center") or {}
        lat, lng = c.get("lat"), c.get("lon")
    if lat is None or lng is None or not name:
        return None
    return {"name": name, "lat": round(float(lat), 6), "lng": round(float(lng), 6)}


def jkn_map():
    rows = supabase.table("locations").select("id, name, latitude, longitude").eq("tier", "state").execute().data or []
    return {r["name"]: r for r in rows}


def pkd_points(jkn_id):
    rows = (
        supabase.table("locations").select("id, latitude, longitude")
        .eq(PARENT_COLUMN, jkn_id).eq("tier", "district").like("name", "PKD %").execute().data or []
    )
    return [{"id": r["id"], "lat": r["latitude"], "lng": r["longitude"]} for r in rows]


def existing_clinic_keys():
    """Rounded-coordinate keys of clinic rows already stored (for dedup)."""
    keys, start = set(), 0
    while True:
        rows = supabase.table("locations").select("latitude, longitude").eq("tier", "clinic").range(start, start + 999).execute().data
        for r in rows:
            keys.add((round(r["latitude"], 4), round(r["longitude"], 4)))
        if len(rows) < 1000:
            break
        start += 1000
    return keys


def main():
    jkns = jkn_map()
    seen = existing_clinic_keys()
    print(f"existing clinic rows (dedup keys): {len(seen)}", flush=True)

    totals = {}
    for state in STATES:
        jkn = jkns.get(state["jkn"])
        if not jkn:
            print(f"! {state['jkn']} missing, skip", flush=True)
            continue
        try:
            data = run_overpass(query(state["osm"]))
        except Exception as exc:
            print(f"  ! {state['jkn']} overpass failed: {exc}", flush=True)
            continue

        pkds = pkd_points(jkn["id"]) or [{"id": None, "lat": jkn["latitude"], "lng": jkn["longitude"]}]
        rows, local_seen = [], set()
        for el in data.get("elements", []):
            e = extract(el)
            if not e:
                continue
            key = (round(e["lat"], 4), round(e["lng"], 4))
            if key in seen or key in local_seen:
                continue
            local_seen.add(key)
            parent = min(pkds, key=lambda p: haversine_km(e["lat"], e["lng"], p["lat"], p["lng"]))["id"] or jkn["id"]
            rows.append({
                "name": e["name"], "tier": "clinic", PARENT_COLUMN: parent,
                "latitude": e["lat"], "longitude": e["lng"], "address": state["osm"],
                "metadata": {"kind": "clinic", "category": classify(e["name"]), "source": "overpass_operator"},
            })

        for i in range(0, len(rows), 200):
            supabase.table("locations").insert(rows[i:i + 200]).execute()

        by_cat = {}
        for r in rows:
            c = r["metadata"]["category"]
            by_cat[c] = by_cat.get(c, 0) + 1
            totals[c] = totals.get(c, 0) + 1
        print(f"  {state['jkn']}: +{len(rows)} new  {by_cat}", flush=True)
        time.sleep(3)

    print(f"\n=== DONE ===  added by category: {totals}", flush=True)


if __name__ == "__main__":
    main()
