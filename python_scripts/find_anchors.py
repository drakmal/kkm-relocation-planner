import os
from pathlib import Path
import requests
from supabase import create_client, Client


def load_env_file():
    env_paths = [Path(__file__).resolve().parents[1] / ".env.local", Path(__file__).resolve().parents[1] / ".env"]
    for env_path in env_paths:
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

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# ------------------------------------------------------------
# Initialize Supabase client
# ------------------------------------------------------------
supabase: Client | None = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
else:
    raise RuntimeError("Please set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in your environment.")


def fetch_residential_anchors(target_lat: float, target_lng: float, radius_km: int = 25):
    """
    Query the OpenStreetMap Overpass API for residential areas, towns, and villages
    within a given radius of the target location.
    """
    overpass_query = f"""
    [out:json][timeout:25];
    (
      node["place"~"^(village|town|suburb|hamlet)$"](around:{radius_km * 1000},{target_lat},{target_lng});
      way["place"~"^(village|town|suburb|hamlet)$"](around:{radius_km * 1000},{target_lat},{target_lng});
      relation["place"~"^(village|town|suburb|hamlet)$"](around:{radius_km * 1000},{target_lat},{target_lng});
    );
    out center;
    """

    headers = {
        "User-Agent": "KKM-Relocation-Planner/1.0",
        "Accept": "application/json",
    }

    try:
        response = requests.post(OVERPASS_URL, data={"data": overpass_query}, headers=headers, timeout=120)
        response.raise_for_status()
    except requests.HTTPError as exc:
        if exc.response.status_code == 406:
            response = requests.get(OVERPASS_URL, params={"data": overpass_query}, headers=headers, timeout=120)
            response.raise_for_status()
        else:
            raise

    data = response.json()

    valid_points = []
    seen = set()

    for element in data.get("elements", []):
        tags = element.get("tags", {}) or {}
        name = tags.get("name") or tags.get("place") or "Unnamed"
        place_type = tags.get("place", "")

        # Filter out obvious farming/empty land areas
        if any(keyword in (tags.get("landuse") or "").lower() for keyword in ["farm", "farmland", "orchard", "forest", "wood", "scrub"]):
            continue

        if place_type not in {"village", "town", "suburb", "hamlet"}:
            continue

        lat = element.get("lat")
        lng = element.get("lon")
        if lat is None or lng is None:
            if element.get("center"):
                lat = element["center"].get("lat")
                lng = element["center"].get("lon")

        if lat is None or lng is None:
            continue

        key = (round(lat, 5), round(lng, 5))
        if key in seen:
            continue
        seen.add(key)

        valid_points.append({
            "name": name,
            "latitude": round(lat, 6),
            "longitude": round(lng, 6),
            "category": place_type,
            "source": "overpass",
            "radius_km": radius_km,
        })

    return valid_points


def save_anchors_to_supabase(tracking_request_id: str, anchors: list[dict]):
    """
    Insert valid anchors into the OriginAnchors table.
    """
    if not supabase:
        raise RuntimeError("Supabase client was not initialized")

    rows = []
    for anchor in anchors:
        rows.append({
            "tracking_request_id": tracking_request_id,
            "name": anchor["name"],
            "latitude": anchor["latitude"],
            "longitude": anchor["longitude"],
            "radius_km": anchor["radius_km"],
            "category": anchor["category"],
            "source": anchor["source"],
        })

    if rows:
        try:
            response = supabase.table("origin_anchors").insert(rows).execute()
            return response
        except Exception as exc:
            print(f"Supabase insert failed due to policy or schema issue: {exc}")
            return None
    return None


if __name__ == "__main__":
    # Example usage:
    # python find_anchors.py <tracking_request_id> <latitude> <longitude>
    import sys

    if len(sys.argv) != 4:
        print("Usage: python find_anchors.py <tracking_request_id> <latitude> <longitude>")
        sys.exit(1)

    tracking_request_id = sys.argv[1]
    target_lat = float(sys.argv[2])
    target_lng = float(sys.argv[3])

    anchors = fetch_residential_anchors(target_lat, target_lng)
    result = save_anchors_to_supabase(tracking_request_id, anchors)
    print(f"Saved {len(anchors)} anchors for request {tracking_request_id}")
    if result:
        print(result)
