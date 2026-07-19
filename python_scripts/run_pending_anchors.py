"""Background anchor creation.

Submitting a tracking request used to call the OpenStreetMap Overpass API inline
(~14 s) inside the Vercel serverless function, which risked timing out. Instead,
the submit route now just saves the request, and this script -- run on a short
GitHub Actions cron (.github/workflows/create_anchors.yml) -- finds any
non-completed request that has no origin_anchors yet and builds them.

Idempotent: a request that already has anchors is skipped, so re-runs are safe.
"""

import os
import time

# find_anchors loads .env(.local) and creates the Supabase client at import.
from find_anchors import supabase, fetch_residential_anchors, save_anchors_to_supabase

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY") or os.getenv("NEXT_PUBLIC_GOOGLE_MAPS_API_KEY")

# Mirrors collect_traffic.py's get_target_office_coordinates() and
# app/lib/originAnchors.ts KNOWN_OFFICES -- kept in sync manually.
KNOWN_OFFICES = {
    "pkd sik": (5.8171, 100.7427),
    "kkm putrajaya": (2.9264, 101.6943),
    "jkn selangor": (3.0738, 101.5183),
    "pkd klang": (3.0408, 101.4473),
}


def resolve_office_coordinates(office_name, office_tier):
    """Known shortcuts -> locations table -> Google Geocoding fallback."""
    if not office_name:
        return None

    normalized = office_name.strip().lower()
    for known_name, coords in KNOWN_OFFICES.items():
        if known_name in normalized:
            return coords

    query = supabase.table("locations").select("latitude, longitude, name, tier")
    if office_tier:
        query = query.eq("tier", office_tier)
    matches = (query.ilike("name", f"%{office_name}%").limit(5).execute().data) or []
    if matches:
        first = matches[0]
        if first.get("latitude") is not None and first.get("longitude") is not None:
            return float(first["latitude"]), float(first["longitude"])

    if GOOGLE_MAPS_API_KEY:
        import requests
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": office_name, "key": GOOGLE_MAPS_API_KEY},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "OK":
            loc = data["results"][0]["geometry"]["location"]
            return float(loc["lat"]), float(loc["lng"])

    return None


def has_anchors(request_id):
    res = (
        supabase.table("origin_anchors")
        .select("id", count="exact", head=True)
        .eq("tracking_request_id", request_id)
        .execute()
    )
    return (res.count or 0) > 0


def main():
    requests_list = (
        supabase.table("tracking_requests")
        .select("id, target_office_name, target_office_tier, status")
        .neq("status", "completed")
        .execute()
        .data
    ) or []

    processed = 0
    created_total = 0
    for req in requests_list:
        if has_anchors(req["id"]):
            continue

        coords = resolve_office_coordinates(req.get("target_office_name"), req.get("target_office_tier"))
        if not coords:
            print(f"request {req['id']}: could not resolve office coordinates, skipping")
            continue

        lat, lng = coords
        try:
            anchors = fetch_residential_anchors(lat, lng)
        except Exception as exc:
            print(f"request {req['id']}: Overpass failed ({exc}); will retry next run")
            continue

        save_anchors_to_supabase(req["id"], anchors)
        processed += 1
        created_total += len(anchors)
        print(f"request {req['id']} ({req.get('target_office_name')}): created {len(anchors)} anchors")
        time.sleep(2)  # be polite to the public Overpass endpoint

    print(f"Done. Built anchors for {processed} request(s); {created_total} anchors total.")


if __name__ == "__main__":
    main()
