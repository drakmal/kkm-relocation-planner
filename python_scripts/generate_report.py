import os
import re
import html
import time
from datetime import datetime, timezone
from pathlib import Path
import requests
from groq import Groq
from dotenv import load_dotenv
from supabase import create_client, Client


load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env.local"), override=False)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"), override=False)

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY") or "YOUR_GROQ_API_KEY"
GROQ_MODEL = "llama-3.3-70b-versatile"

# Resend email delivery (https://resend.com). Set these in the environment /
# GitHub Actions secrets. RESEND_FROM must be a verified sender domain.
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_FROM = os.getenv("RESEND_FROM", "Relocation Planner <onboarding@resend.dev>")
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
else:
    raise RuntimeError("Please set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in your environment.")


def fetch_tracking_logs(tracking_request_id: str):
    """
    Fetch all daily traffic logs for a specific tracking request. Tries to
    include distance_km (db/006_add_distance_km.sql); falls back to without it
    if that migration has not been run yet, so this keeps working either way.
    """
    if not supabase:
        raise RuntimeError("Supabase client not initialized")

    base_cols = "log_date, travel_time_minutes, weather_temp_c, weather_condition, weather_wind_kmh, weather_precip_mm, origin_anchors(name, latitude, longitude)"
    try:
        response = (
            supabase.table("daily_traffic_logs")
            .select(f"distance_km, {base_cols}")
            .eq("tracking_request_id", tracking_request_id)
            .order("log_date", desc=False)
            .execute()
        )
    except Exception:
        response = (
            supabase.table("daily_traffic_logs")
            .select(base_cols)
            .eq("tracking_request_id", tracking_request_id)
            .order("log_date", desc=False)
            .execute()
        )

    return response.data or []


def filter_logs_for_analysis(log_rows: list[dict]):
    """
    Keep only travel logs that are plausible for a commute: exclude very long
    outliers, and (when distance_km is available) exclude readings whose
    implied speed is physically implausible -- e.g. a "3 min" reading several
    km away, which is almost always a bad anchor coordinate or a stale API
    response, not a real fast commute.
    """
    filtered = []
    for row in log_rows:
        travel_time = row.get("travel_time_minutes")
        try:
            if travel_time is None or int(travel_time) > 120:
                continue
        except (TypeError, ValueError):
            continue

        distance_km = row.get("distance_km")
        if distance_km is not None and travel_time:
            try:
                implied_speed_kmh = float(distance_km) / (float(travel_time) / 60)
                if implied_speed_kmh > MAX_PLAUSIBLE_SPEED_KMH:
                    continue
            except (TypeError, ValueError, ZeroDivisionError):
                pass

        filtered.append(row)
    return filtered


# `weather_condition` stores Open-Meteo's raw WMO weather_code (see
# collect_traffic.py) -- a bare integer means nothing to the model, so map the
# codes we actually see in Malaysia to short labels. Anything unmapped falls
# back to the code itself rather than guessing at a description.
WMO_CONDITIONS = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog", 48: "fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    80: "light showers", 81: "showers", 82: "violent showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


def describe_weather_code(code) -> str | None:
    """Short text for a WMO weather code; falls back to 'code N' if unmapped."""
    if code is None:
        return None
    try:
        code = int(code)
    except (TypeError, ValueError):
        return str(code)
    return WMO_CONDITIONS.get(code, f"code {code}")


def format_logs_for_prompt(log_rows: list[dict]):
    """
    Condense log rows into a per-day summary for the AI -- one line per collected
    date, not one per log row.

    A request with many anchors over a long window produces hundreds of rows, and
    dumping them raw scales the prompt past Groq's free-tier tokens-per-minute
    ceiling (a 431-log request measured ~21k tokens against a 12k limit, so its
    report failed silently every hour for days). The per-area detail the report
    actually cites already comes from summarize_anchor_reliability(); this block
    only has to carry the day-to-day spread and the weather picture, and both
    aggregate cleanly. Size is now bounded by the number of tracked days (1-5),
    not by the anchor count.
    """
    if not log_rows:
        return "No traffic logs available after filtering out routes longer than 120 minutes."

    by_date: dict[str, dict] = {}
    for row in log_rows:
        entry = by_date.setdefault(
            row["log_date"],
            {"minutes": [], "temp": [], "wind": [], "precip": [], "conditions": []},
        )
        minutes = row.get("travel_time_minutes")
        if minutes is not None:
            entry["minutes"].append(float(minutes))
        for key, column in (("temp", "weather_temp_c"), ("wind", "weather_wind_kmh"), ("precip", "weather_precip_mm")):
            value = row.get(column)
            if value is not None:
                entry[key].append(float(value))
        condition = row.get("weather_condition")
        if condition:
            entry["conditions"].append(condition)

    def _avg(values):
        return sum(values) / len(values) if values else None

    lines = []
    for date in sorted(by_date):
        entry = by_date[date]
        minutes = entry["minutes"]
        if minutes:
            travel = (
                f"travel avg {_avg(minutes):.0f} min "
                f"(range {min(minutes):.0f}-{max(minutes):.0f} across {len(minutes)} area samples)"
            )
        else:
            travel = "no travel times recorded"

        weather = []
        if entry["conditions"]:
            dominant = max(set(entry["conditions"]), key=entry["conditions"].count)
            label = describe_weather_code(dominant)
            if label:
                weather.append(label)
        if entry["temp"]:
            weather.append(f"temp {_avg(entry['temp']):.0f}C")
        if entry["wind"]:
            weather.append(f"wind {_avg(entry['wind']):.0f} km/h")
        if entry["precip"]:
            weather.append(f"precip {_avg(entry['precip']):.1f} mm")

        lines.append(f"Date: {date} | {travel} | Weather: {', '.join(weather) if weather else 'not recorded'}")
    return "\n".join(lines)


# A route averaging below this speed likely isn't a direct highway/main-road
# hop -- more probably backroads, junctions, or recurring congestion. Above it,
# the route likely runs on a direct/highway-fed road. This is an INFERENCE from
# average speed (distance / time), not real road-network data -- we don't have
# a Routes/Directions API call to name actual roads or tolls, so the prompt
# must present it as a heuristic, never as fact.
INDIRECT_ROUTE_SPEED_KMH = 30
# A reading implying a faster average speed than this is treated as a bad
# measurement (wrong/duplicate anchor coordinate, stale API response, etc.)
# rather than a real commute, and is dropped in filter_logs_for_analysis.
# 110 km/h is generous for Malaysian highway driving with traffic, so this
# only catches genuinely implausible readings.
MAX_PLAUSIBLE_SPEED_KMH = 110
# Fallback ceiling for the "farthest area still within a commutable travel
# time" pick, used only when the user's own leave/arrive times aren't
# available to derive a real budget (see summarize_anchor_reliability) -- an
# hour one-way is a common upper bound used elsewhere in this codebase's
# Distance Matrix sanity check (90 min hard cap; here we use a stricter, more
# realistic "still worth living there" bar).
FARTHEST_COMMUTABLE_MAX_MIN = 60

# Two anchors within this distance of each other are treated as the same real
# place (e.g. duplicate/near-duplicate OSM nodes with slightly different
# names/coordinates for one settlement), and merged before aggregation.
ANCHOR_MERGE_DISTANCE_KM = 1.5
# A looser distance bound used only when names also indicate the same place
# (one name is a substring of the other) -- guards against merging two
# genuinely different, same-named settlements on opposite sides of the
# ~25 km anchor radius.
ANCHOR_MERGE_NAME_MATCH_DISTANCE_KM = 5.0


def commute_budget_minutes(leave_home: str | None, arrive_office: str | None) -> int | None:
    """
    Minutes between the user's stated leave-home and arrive-office clock
    times ("HH:MM"), used as the real ceiling for the farthest-area pick
    instead of a fixed constant. Returns None if either time is missing,
    unparseable, or arrive_office isn't after leave_home same-day -- callers
    should fall back to FARTHEST_COMMUTABLE_MAX_MIN in that case.
    """
    if not leave_home or not arrive_office:
        return None
    try:
        lh_h, lh_m = (int(p) for p in leave_home.split(":")[:2])
        ao_h, ao_m = (int(p) for p in arrive_office.split(":")[:2])
    except (ValueError, AttributeError):
        return None
    diff = (ao_h * 60 + ao_m) - (lh_h * 60 + lh_m)
    return diff if diff > 0 else None


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    from math import radians, sin, cos, sqrt, atan2

    earth_radius_km = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lng2 - lng1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlambda / 2) ** 2
    return 2 * earth_radius_km * atan2(sqrt(a), sqrt(1 - a))


def _merge_duplicate_anchors(anchors: list[dict]) -> list[dict]:
    """
    Merge anchors that almost certainly represent the same real place --
    OSM sometimes has two nodes for one settlement with slightly different
    names/coordinates (e.g. "Dulang Kechil" and "Kampung Kuala Dulang
    Kechil" a few hundred metres apart). Anchors within
    ANCHOR_MERGE_DISTANCE_KM are always merged; anchors within the looser
    ANCHOR_MERGE_NAME_MATCH_DISTANCE_KM are merged only if one name is a
    substring of the other. The longest (most descriptive) name in each
    merged cluster becomes the canonical display name.
    """
    n = len(anchors)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    def names_overlap(a: str, b: str) -> bool:
        a_low, b_low = a.lower(), b.lower()
        return a_low in b_low or b_low in a_low

    for i in range(n):
        for j in range(i + 1, n):
            a, b = anchors[i], anchors[j]
            if a["lat"] is None or a["lng"] is None or b["lat"] is None or b["lng"] is None:
                continue
            dist = _haversine_km(a["lat"], a["lng"], b["lat"], b["lng"])
            if dist <= ANCHOR_MERGE_DISTANCE_KM:
                union(i, j)
            elif dist <= ANCHOR_MERGE_NAME_MATCH_DISTANCE_KM and names_overlap(a["name"], b["name"]):
                union(i, j)

    clusters: dict[int, dict] = {}
    for i, a in enumerate(anchors):
        cluster = clusters.setdefault(find(i), {"names": [], "minutes": [], "distances": []})
        cluster["names"].append(a["name"])
        cluster["minutes"].extend(a["minutes"])
        cluster["distances"].extend(a["distances"])

    merged = []
    for cluster in clusters.values():
        merged.append({
            "name": max(cluster["names"], key=len),
            "minutes": cluster["minutes"],
            "distances": cluster["distances"],
        })
    return merged


def summarize_anchor_reliability(log_rows: list[dict], leave_home: str | None = None, arrive_office: str | None = None):
    """
    Aggregate per-anchor stats (avg travel time, avg distance, avg speed) across
    all collected days -- after merging near-duplicate anchors that represent
    the same real place -- then pick the top-3 most reliable anchors and the
    single farthest anchor that's still commutable within the user's own
    leave-home/arrive-office budget (falling back to FARTHEST_COMMUTABLE_MAX_MIN
    if that budget isn't available). Returns (summary_text, ranked_anchor_names)
    -- ranked_anchor_names feeds the rental-estimate ask.
    """
    raw: dict[tuple, dict] = {}
    for row in log_rows:
        origin = row.get("origin_anchors")
        if not isinstance(origin, dict):
            continue
        name = origin.get("name")
        if not name:
            continue
        minutes = row.get("travel_time_minutes")
        if minutes is None:
            continue
        lat, lng = origin.get("latitude"), origin.get("longitude")
        key = (name, round(lat, 6) if lat is not None else None, round(lng, 6) if lng is not None else None)
        entry = raw.setdefault(key, {"name": name, "lat": lat, "lng": lng, "minutes": [], "distances": []})
        entry["minutes"].append(float(minutes))
        distance = row.get("distance_km")
        if distance is not None:
            entry["distances"].append(float(distance))

    if not raw:
        return "No per-area statistics available.", []

    stats: dict[str, dict] = {}
    for cluster in _merge_duplicate_anchors(list(raw.values())):
        entry = stats.setdefault(cluster["name"], {"minutes": [], "distances": []})
        entry["minutes"].extend(cluster["minutes"])
        entry["distances"].extend(cluster["distances"])

    rows_out = []
    for name, entry in stats.items():
        avg_min = sum(entry["minutes"]) / len(entry["minutes"])
        avg_km = sum(entry["distances"]) / len(entry["distances"]) if entry["distances"] else None
        speed_kmh = (avg_km / (avg_min / 60)) if avg_km and avg_min else None
        rows_out.append({"name": name, "avg_min": avg_min, "avg_km": avg_km, "speed_kmh": speed_kmh, "n": len(entry["minutes"])})

    by_reliability = sorted(rows_out, key=lambda r: r["avg_min"])
    top3 = by_reliability[:3]

    farthest_max_min = commute_budget_minutes(leave_home, arrive_office) or FARTHEST_COMMUTABLE_MAX_MIN
    commutable = [r for r in rows_out if r["avg_min"] <= farthest_max_min and r["avg_km"] is not None]
    farthest = max(commutable, key=lambda r: r["avg_km"]) if commutable else None

    def route_note(r):
        if r["speed_kmh"] is None:
            return ""
        if r["speed_kmh"] < INDIRECT_ROUTE_SPEED_KMH:
            return " (low avg speed -- route likely indirect/congested, not a direct highway hop; verify road access before deciding)"
        return " (higher avg speed -- route likely runs on a direct/main road)"

    lines = ["Most reliable areas (by average commute time, all collected days):"]
    for r in top3:
        km_txt = f", ~{r['avg_km']:.1f} km" if r["avg_km"] is not None else ""
        lines.append(f"- {r['name']}: avg {r['avg_min']:.0f} min{km_txt} over {r['n']} day(s){route_note(r)}")

    if farthest and farthest["name"] not in {r["name"] for r in top3}:
        lines.append(
            f"\nFarthest area still within a {farthest_max_min}-minute commute: "
            f"{farthest['name']} (~{farthest['avg_km']:.1f} km, avg {farthest['avg_min']:.0f} min){route_note(farthest)}"
        )
    elif farthest:
        lines.append(f"\n({farthest['name']} above is also the farthest area still within a {farthest_max_min}-minute commute.)")

    ranked_names = [r["name"] for r in top3] + ([farthest["name"]] if farthest else [])
    # de-dup while preserving order
    seen = set()
    ranked_names = [n for n in ranked_names if not (n in seen or seen.add(n))]

    return "\n".join(lines), ranked_names


def generate_report_with_groq(prompt: str):
    """
    Send the formatted data to the Groq API and return the analysis text.
    """
    if GROQ_API_KEY == "YOUR_GROQ_API_KEY":
        raise RuntimeError("Please insert your Groq API key in the script or environment.")

    client = Groq(api_key=GROQ_API_KEY)

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "You are a practical transport analyst for Malaysian health workers."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=600,
            )
            return response.choices[0].message.content
        except Exception as exc:
            if attempt < 2 and "429" in str(exc):
                time.sleep(10)
                continue
            raise RuntimeError(f"Groq API request failed: {exc}")

    raise RuntimeError("Groq API request failed after retries.")


def build_prompt(tracking_request_id: str, formatted_logs: str, anchor_summary: str = "", ranked_names: list[str] | None = None,
                 office_name: str = "the target office",
                 leave_home: str | None = None, arrive_office: str | None = None):
    context = f"Target office: {office_name}."
    if leave_home or arrive_office:
        context += (
            f" The user has already fixed their schedule: leave home at {leave_home or '—'} and "
            f"arrive at the office by {arrive_office or '—'}. Treat these as given facts, not as "
            f"something to recompute or second-guess -- do not propose a different departure time "
            f"and do not add a generic disclaimer such as \"this may vary due to traffic or weather\"."
        )

    rental_ask = ""
    if ranked_names:
        area_list = ", ".join(ranked_names)
        rental_ask = f"""
6. After the narrative, add a line "###AREA_ESTIMATES###" followed by one line per
   area in this exact list: {area_list}
   Format each line exactly as: Area Name | RM low-high
   Give a plausible TYPICAL monthly rental range for a modest home in that area,
   based on general knowledge of Malaysian rental markets. Label it clearly as an
   estimate elsewhere in your narrative -- this is NOT live listing data.
"""

    return f"""
You are a transport analyst for Malaysian health workers.
Analyze the following commute tracking data for tracking request {tracking_request_id}.
{context}

Use the travel times and weather conditions to identify which residential areas are most reliable for travel to the target office.
Factor in weather delays from wind, rain, and temperature where relevant.

Per-area reliability summary (already computed from the collected data):
{anchor_summary}

The "avg speed" note on each area is an INFERENCE from distance/time, not real
road data -- you do NOT have information about actual road names, number of
access roads, or tolls. Never invent a road name or toll amount. You MAY say a
route "appears direct/highway-like" or "appears indirect, possibly congested,
worth verifying access roads" based on the note given, but phrase it as an
inference, not a fact.

Return a clean text report with:
1. A short summary of the overall pattern.
2. The top 3 residential areas ranked by reliability (use the summary above).
3. The farthest area that is still within a commutable travel time (use the summary above), so the user knows how far they could realistically live.
4. Keep the report concise and practical.
5. Ignore any travel times that appear unrealistic or above 120 minutes.
{rental_ask}
Day-by-day overview (each line aggregates every area sampled that day):
{formatted_logs}
"""


def save_report_to_file(report: str, tracking_request_id: str) -> Path:
    """
    Save the final AI report as a text file in the reports/ folder.
    """
    reports_dir = Path(__file__).resolve().parent.parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    output_path = reports_dir / f"{tracking_request_id}.txt"
    output_path.write_text(report, encoding="utf-8")
    return output_path


def parse_area_estimates(report: str) -> dict:
    """
    Pull the "###AREA_ESTIMATES###" block (see build_prompt) out of the Groq
    response into {"area name lowercased": {"low": int, "high": int}}. Returns
    {} if the marker is absent or a line doesn't match -- best-effort, since
    this is free-text LLM output, not a guaranteed structured response.
    """
    marker = "###AREA_ESTIMATES###"
    if marker not in report:
        return {}
    block = report.split(marker, 1)[1]
    estimates = {}
    for line in block.splitlines():
        m = re.match(r"\s*-?\s*(.+?)\s*\|\s*RM\s*([\d,]+)\s*-\s*([\d,]+)", line)
        if not m:
            continue
        name, low, high = m.groups()
        estimates[name.strip().lower()] = {"low": int(low.replace(",", "")), "high": int(high.replace(",", ""))}
    return estimates


def save_report_to_db(report: str, tracking_request_id: str, area_price_estimates: dict | None = None) -> None:
    """
    Persist the report back onto the tracking_requests row so the web app can
    display it. Without this, the report only exists as a local file and is
    invisible to users (and lost when run on an ephemeral CI runner).
    """
    if not supabase:
        raise RuntimeError("Supabase client not initialized")

    payload = {
        "report_text": report,
        "report_generated_at": datetime.now(timezone.utc).isoformat(),
        # Must match the tracking_requests.status check constraint
        # ('pending' | 'active' | 'completed' | 'cancelled').
        "status": "completed",
    }
    query = supabase.table("tracking_requests").update(payload).eq("id", tracking_request_id)
    try:
        # area_price_estimates (db/007_add_rental_estimates.sql) is best-effort:
        # if that migration hasn't run yet, retry without it so the report still saves.
        if area_price_estimates:
            supabase.table("tracking_requests").update({**payload, "area_price_estimates": area_price_estimates}).eq("id", tracking_request_id).execute()
        else:
            query.execute()
    except Exception:
        query.execute()


def send_report_email(
    to_email: str,
    office_name: str,
    report: str,
    tracking_request_id: str,
    leave_home: str | None = None,
    arrive_office: str | None = None,
) -> bool:
    """Email the finished report via Resend. No-op (returns False) if unconfigured."""
    if not RESEND_API_KEY:
        print("RESEND_API_KEY not set — skipping email (report still saved to DB).")
        return False

    report_url = f"{APP_BASE_URL}/report/{tracking_request_id}" if APP_BASE_URL else None
    safe_report = html.escape(report)
    link_html = f"<p><a href='{report_url}'>View the full report online</a></p>" if report_url else ""
    times_html = (
        f"<p>Leave home: <strong>{html.escape(leave_home)}</strong> &middot; "
        f"Arrive office: <strong>{html.escape(arrive_office)}</strong></p>"
        if leave_home and arrive_office else ""
    )
    body_html = (
        "<div style=\"font-family:Segoe UI,Arial,sans-serif;color:#16324f;max-width:640px\">"
        "<h2 style=\"color:#0f4c92\">Your relocation report</h2>"
        f"<p>Target office: <strong>{html.escape(office_name)}</strong></p>"
        f"{times_html}"
        "<pre style=\"white-space:pre-wrap;background:#f7fbff;border:1px solid #dce9f6;"
        f"border-radius:12px;padding:16px;line-height:1.6\">{safe_report}</pre>"
        f"{link_html}"
        "</div>"
    )
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={
                "from": RESEND_FROM,
                "to": [to_email],
                "subject": f"Your relocation report — {office_name}",
                "html": body_html,
                "text": report,
            },
            timeout=30,
        )
        ok = resp.status_code in (200, 201)
        print(f"Email to {to_email}: HTTP {resp.status_code}{'' if ok else ' — ' + resp.text[:200]}")
        return ok
    except requests.RequestException as exc:
        print(f"Email send failed: {exc}")
        return False


def process_request(tracking_request_id: str) -> str:
    """Full pipeline for one request: build report, persist, and email it."""
    request_row = (
        supabase.table("tracking_requests")
        .select("user_email, target_office_name, arrival_time, return_time")
        .eq("id", tracking_request_id)
        .maybe_single()
        .execute()
        .data
    ) or {}

    leave_home = (request_row.get("return_time") or "")[:5] or None    # return_time = leave-home
    arrive_office = (request_row.get("arrival_time") or "")[:5] or None

    logs = fetch_tracking_logs(tracking_request_id)
    filtered_logs = filter_logs_for_analysis(logs)
    formatted_logs = format_logs_for_prompt(filtered_logs)
    anchor_summary, ranked_names = summarize_anchor_reliability(filtered_logs, leave_home=leave_home, arrive_office=arrive_office)
    prompt = build_prompt(
        tracking_request_id,
        formatted_logs,
        anchor_summary=anchor_summary,
        ranked_names=ranked_names,
        office_name=request_row.get("target_office_name") or "the target office",
        leave_home=leave_home,
        arrive_office=arrive_office,
    )
    report = generate_report_with_groq(prompt)
    area_price_estimates = parse_area_estimates(report)

    save_report_to_file(report, tracking_request_id)
    save_report_to_db(report, tracking_request_id, area_price_estimates)

    email = request_row.get("user_email")
    if email:
        send_report_email(
            email,
            request_row.get("target_office_name") or "your target office",
            report,
            tracking_request_id,
            leave_home=leave_home,
            arrive_office=arrive_office,
        )

    return report


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python generate_report.py <tracking_request_id>")
        sys.exit(1)

    result = process_request(sys.argv[1])
    print(result)
    print(f"\nDone for tracking_requests row {sys.argv[1]}")
