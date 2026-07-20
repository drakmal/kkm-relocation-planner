import os
import json
from datetime import datetime, date, timedelta, timezone
import requests
from dotenv import load_dotenv
from supabase import create_client, Client


def skip_reason_today():
    """Return a reason string if collection should be skipped today (weekend or
    Malaysia public holiday, in MYT), else None."""
    today = datetime.now(timezone(timedelta(hours=8))).date()
    if today.weekday() >= 5:
        return f"weekend ({today.isoformat()})"
    holidays_path = os.path.join(os.path.dirname(__file__), "..", "data", "public_holidays_my.json")
    try:
        with open(holidays_path, encoding="utf-8") as f:
            holidays = {h["date"]: h["name"] for h in json.load(f).get("holidays", [])}
        if today.isoformat() in holidays:
            return f"public holiday — {holidays[today.isoformat()]} ({today.isoformat()})"
    except (OSError, ValueError, KeyError):
        pass
    return None


load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env.local"), override=False)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"), override=False)

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")

# IMPORTANT: Insert your Google Maps API key here.
# You can also place it in your environment as GOOGLE_MAPS_API_KEY.
GOOGLE_MAPS_API_KEY = (
    os.getenv("GOOGLE_MAPS_API_KEY")
    or os.getenv("NEXT_PUBLIC_GOOGLE_MAPS_API_KEY")
    or os.getenv("GOOGLE_MAPS_API_KEY_FROM_ENV")
    or "YOUR_GOOGLE_MAPS_API_KEY"
)

# ------------------------------------------------------------
# Initialize Supabase client
# ------------------------------------------------------------
supabase: Client | None = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
else:
    raise RuntimeError("Please set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in your environment.")


def get_origin_anchors():
    """
    Load all origin anchors from Supabase.
    """
    if not supabase:
        raise RuntimeError("Supabase client was not initialized")

    response = supabase.table("origin_anchors").select("id, tracking_request_id, latitude, longitude, name").execute()
    return response.data or []


def get_tracking_requests():
    """
    Load open tracking requests (not yet completed) with the fields needed to
    decide whether to collect now: target office, the tracking window
    (tracking_duration = 'YYYY-MM-DD to YYYY-MM-DD') and the leave-home time
    (stored in return_time).
    """
    if not supabase:
        raise RuntimeError("Supabase client was not initialized")

    response = (
        supabase.table("tracking_requests")
        .select("id, target_office_name, target_office_tier, tracking_duration, return_time, status")
        .neq("status", "completed")
        .execute()
    )
    return response.data or []


import re as _re


def parse_window(tracking_duration):
    """Return (start_date, end_date) as date objects, or (None, None)."""
    m = _re.search(r"(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})", tracking_duration or "")
    if not m:
        return None, None
    return date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))


def minutes_until_leave(now_myt, leave_home_str):
    """Minutes from now until today's leave-home time (negative if already past)."""
    try:
        hh, mm = (leave_home_str or "06:30")[:5].split(":")
        leave = now_myt.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except (ValueError, AttributeError):
        return None
    return (leave - now_myt).total_seconds() / 60.0


def already_collected_today(request_id, today):
    if not supabase:
        return False
    res = (
        supabase.table("daily_traffic_logs")
        .select("id", count="exact", head=True)
        .eq("tracking_request_id", request_id)
        .eq("log_date", today.isoformat())
        .execute()
    )
    return (res.count or 0) > 0


def get_anchors_for_request(request_id):
    if not supabase:
        return []
    res = (
        supabase.table("origin_anchors")
        .select("id, tracking_request_id, latitude, longitude, name")
        .eq("tracking_request_id", request_id)
        .execute()
    )
    return res.data or []


def get_target_office_coordinates(tracking_request: dict):
    """
    Resolve the target office coordinates for a tracking request from the locations table,
    or fall back to Google Geocoding if no matching location row is available.
    """
    if not supabase:
        raise RuntimeError("Supabase client was not initialized")

    office_name = tracking_request.get("target_office_name") or ""
    office_tier = tracking_request.get("target_office_tier") or ""

    if not office_name:
        return None

    known_offices = {
        "pkd sik": (5.8171, 100.7427),
        "kkm putrajaya": (2.9264, 101.6943),
        "jkn selangor": (3.0738, 101.5183),
        "pkd klang": (3.0408, 101.4473),
    }
    normalized_name = office_name.strip().lower()
    for known_name, coords in known_offices.items():
        if known_name in normalized_name:
            return coords

    query = supabase.table("locations").select("latitude, longitude, name, tier")
    if office_tier:
        query = query.eq("tier", office_tier)
    query = query.ilike("name", f"%{office_name}%")
    result = query.limit(5).execute()
    matches = result.data or []

    if matches:
        first_match = matches[0]
        return float(first_match["latitude"]), float(first_match["longitude"])

    geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": office_name,
        "key": GOOGLE_MAPS_API_KEY,
    }
    response = requests.get(geocode_url, params=params, timeout=60)
    response.raise_for_status()
    geocode_data = response.json()
    if geocode_data.get("status") != "OK":
        return None

    location = geocode_data["results"][0]["geometry"]["location"]
    return float(location["lat"]), float(location["lng"])


def get_google_maps_travel_time(origin_lat: float, origin_lng: float, destination_lat: float, destination_lng: float, departure_time: datetime, print_raw_response: bool = False):
    """
    Call the Google Maps Distance Matrix API and return the travel time in minutes.
    """
    if GOOGLE_MAPS_API_KEY == "YOUR_GOOGLE_MAPS_API_KEY":
        raise RuntimeError("Please insert your Google Maps API key in the script or environment.")

    # Distance Matrix rejects a departure_time in the past ("INVALID_REQUEST ...
    # departure_time is in the past"). `departure_time` is computed once at the
    # start of the run, but a batch of 100+ anchors takes minutes, so by the time
    # this call fires the leave-home moment may already be past. Clamp to now (+1
    # min buffer) so we always get current-traffic instead of a hard failure.
    now_utc = datetime.now(timezone.utc)
    if departure_time < now_utc:
        departure_time = now_utc + timedelta(minutes=1)

    url = "https://maps.googleapis.com/maps/api/distancematrix/json"
    travel_mode = "driving"
    params = {
        "origins": f"{origin_lat},{origin_lng}",
        "destinations": f"{destination_lat},{destination_lng}",
        "mode": travel_mode,
        "departure_time": str(int(departure_time.timestamp())),
        "traffic_model": "best_guess",
        "key": GOOGLE_MAPS_API_KEY,
    }

    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    data = response.json()

    if print_raw_response:
        print("Google Maps raw response:")
        print(data)

    if data.get("status") != "OK":
        raise RuntimeError(f"Google Maps API error: {data.get('status')} - {data.get('error_message')}")

    element = data["rows"][0]["elements"][0]
    if element.get("status") != "OK":
        raise RuntimeError(f"Distance Matrix element error: {element.get('status')}")

    duration_in_traffic = element.get("duration_in_traffic", element.get("duration", {}))
    if isinstance(duration_in_traffic, dict):
        travel_time_minutes = duration_in_traffic.get("value", 0) // 60
    else:
        travel_time_minutes = int(duration_in_traffic)

    if travel_time_minutes > 90:
        raise ValueError(f"Sanity check failed: travel time {travel_time_minutes} minutes exceeds 90-minute limit")

    return travel_time_minutes


def get_weather_for_location(lat: float, lng: float):
    """
    Call the Open-Meteo API for current weather at the anchor location.
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lng,
        "current": "temperature_2m,precipitation,wind_speed_10m,weather_code",
        "timezone": "auto",
    }

    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    data = response.json()
    current = data.get("current", {})
    return {
        "temp_c": current.get("temperature_2m"),
        "weather_condition": current.get("weather_code"),
        "wind_kmh": current.get("wind_speed_10m"),
        "precip_mm": current.get("precipitation"),
    }


def save_daily_logs(tracking_request_id: str, anchor_id: str, log_date: date, travel_time_minutes: int, weather_data: dict):
    """
    Save the traffic and weather summary into the DailyTrafficLogs table.
    """
    if not supabase:
        raise RuntimeError("Supabase client was not initialized")

    payload = {
        "tracking_request_id": tracking_request_id,
        "origin_anchor_id": anchor_id,
        "log_date": log_date.isoformat(),
        "travel_time_minutes": travel_time_minutes,
        "weather_temp_c": weather_data.get("temp_c"),
        "weather_condition": weather_data.get("weather_condition"),
        "weather_wind_kmh": weather_data.get("wind_kmh"),
        "weather_precip_mm": weather_data.get("precip_mm"),
        "source": "google_maps_openmeteo",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    supabase.table("daily_traffic_logs").insert(payload).execute()


# How close to the leave-home time we collect. This script is meant to run
# every ~15 min; we collect on the tick that falls in [leave-home - 30 min,
# leave-home + 10 min], then dedupe so each request is collected once per day.
COLLECT_LEAD_MAX_MIN = 30   # start up to 30 min before leave-home
COLLECT_LEAD_MIN_MIN = -10  # ...still allow up to 10 min after (cron jitter)

MYT = timezone(timedelta(hours=8))


def collect_for_request(request, now_myt, today):
    anchors = get_anchors_for_request(request["id"])
    if not anchors:
        print(f"  request {request['id']}: no anchors, skipping")
        return

    target_coords = get_target_office_coordinates(request)
    if not target_coords:
        print(f"  request {request['id']}: no target office coordinates, skipping")
        return
    target_lat, target_lng = target_coords

    # Predict traffic at the actual leave-home moment (must be >= now for the
    # traffic-aware Distance Matrix; if we're already past it, use now).
    hh, mm = (request.get("return_time") or "06:30")[:5].split(":")
    leave_dt = now_myt.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    departure_time = max(leave_dt, now_myt)

    logged = 0
    for index, anchor in enumerate(anchors):
        try:
            travel_time = get_google_maps_travel_time(
                anchor["latitude"], anchor["longitude"], target_lat, target_lng,
                departure_time, print_raw_response=(index == 0),
            )
            weather = get_weather_for_location(anchor["latitude"], anchor["longitude"])
            save_daily_logs(request["id"], anchor["id"], today, travel_time, weather)
            logged += 1
        except Exception as exc:
            print(f"    anchor {anchor['name']}: {exc}")
    print(f"  request {request['id']} ({request.get('target_office_name')}): logged {logged}/{len(anchors)} anchors")


if __name__ == "__main__":
    _skip = skip_reason_today()
    if _skip:
        print(f"Skipping traffic collection today: {_skip}")
        raise SystemExit(0)

    now = datetime.now(MYT)
    today = now.date()

    requests_list = get_tracking_requests()
    processed = 0
    for request in requests_list:
        start, end = parse_window(request.get("tracking_duration"))
        if start and end and not (start <= today <= end):
            continue  # outside this request's tracking window
        mins = minutes_until_leave(now, request.get("return_time"))
        if mins is None or not (COLLECT_LEAD_MIN_MIN <= mins <= COLLECT_LEAD_MAX_MIN):
            continue  # not near this user's leave-home time yet
        if already_collected_today(request["id"], today):
            continue  # already have today's data point

        collect_for_request(request, now, today)
        processed += 1

    print(f"Done. Collected for {processed} request(s) at {now.isoformat()}.")
