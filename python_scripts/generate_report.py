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
RESEND_FROM = os.getenv("RESEND_FROM", "KKM Relocation Planner <onboarding@resend.dev>")
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

    base_cols = "log_date, travel_time_minutes, weather_temp_c, weather_condition, weather_wind_kmh, weather_precip_mm, origin_anchors(name)"
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
    Keep only travel logs that are plausible for a commute and exclude very long outliers.
    """
    filtered = []
    for row in log_rows:
        travel_time = row.get("travel_time_minutes")
        try:
            if travel_time is not None and int(travel_time) <= 120:
                filtered.append(row)
        except (TypeError, ValueError):
            continue
    return filtered


def format_logs_for_prompt(log_rows: list[dict]):
    """
    Convert log rows into a readable text summary for the AI.
    """
    if not log_rows:
        return "No traffic logs available after filtering out routes longer than 120 minutes."

    lines = []
    for row in log_rows:
        anchor_name = "Unknown"
        origin = row.get("origin_anchors")
        if isinstance(origin, dict):
            anchor_name = origin.get("name", "Unknown")

        lines.append(
            f"Date: {row['log_date']} | Anchor: {anchor_name} | Travel: {row.get('travel_time_minutes')} min | "
            f"Weather: temp {row.get('weather_temp_c')}C, wind {row.get('weather_wind_kmh')} km/h, precip {row.get('weather_precip_mm')} mm"
        )
    return "\n".join(lines)


# A route averaging below this speed likely isn't a direct highway/main-road
# hop -- more probably backroads, junctions, or recurring congestion. Above it,
# the route likely runs on a direct/highway-fed road. This is an INFERENCE from
# average speed (distance / time), not real road-network data -- we don't have
# a Routes/Directions API call to name actual roads or tolls, so the prompt
# must present it as a heuristic, never as fact.
INDIRECT_ROUTE_SPEED_KMH = 30
# Farthest-anchor pick is capped to anchors whose average travel time is still
# "commutable" for a health worker -- an hour one-way is a common upper bound
# used elsewhere in this codebase's Distance Matrix sanity check (90 min hard
# cap; here we use a stricter, more realistic "still worth living there" bar).
FARTHEST_COMMUTABLE_MAX_MIN = 60


def summarize_anchor_reliability(log_rows: list[dict]):
    """
    Aggregate per-anchor stats (avg travel time, avg distance, avg speed) across
    all collected days, then pick the top-3 most reliable anchors and the
    single farthest anchor that's still commutable. Returns (summary_text,
    ranked_anchor_names) -- ranked_anchor_names feeds the rental-estimate ask.
    """
    stats: dict[str, dict] = {}
    for row in log_rows:
        origin = row.get("origin_anchors")
        name = origin.get("name") if isinstance(origin, dict) else None
        if not name:
            continue
        minutes = row.get("travel_time_minutes")
        if minutes is None:
            continue
        entry = stats.setdefault(name, {"minutes": [], "distances": []})
        entry["minutes"].append(float(minutes))
        distance = row.get("distance_km")
        if distance is not None:
            entry["distances"].append(float(distance))

    if not stats:
        return "No per-area statistics available.", []

    rows_out = []
    for name, entry in stats.items():
        avg_min = sum(entry["minutes"]) / len(entry["minutes"])
        avg_km = sum(entry["distances"]) / len(entry["distances"]) if entry["distances"] else None
        speed_kmh = (avg_km / (avg_min / 60)) if avg_km and avg_min else None
        rows_out.append({"name": name, "avg_min": avg_min, "avg_km": avg_km, "speed_kmh": speed_kmh, "n": len(entry["minutes"])})

    by_reliability = sorted(rows_out, key=lambda r: r["avg_min"])
    top3 = by_reliability[:3]

    commutable = [r for r in rows_out if r["avg_min"] <= FARTHEST_COMMUTABLE_MAX_MIN and r["avg_km"] is not None]
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
            f"\nFarthest area still within a {FARTHEST_COMMUTABLE_MAX_MIN}-minute commute: "
            f"{farthest['name']} (~{farthest['avg_km']:.1f} km, avg {farthest['avg_min']:.0f} min){route_note(farthest)}"
        )
    elif farthest:
        lines.append(f"\n({farthest['name']} above is also the farthest area still within a {FARTHEST_COMMUTABLE_MAX_MIN}-minute commute.)")

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
            f" The user plans to leave home at {leave_home or '—'} and arrive at the office by "
            f"{arrive_office or '—'}. Anchor your departure-time advice to these times."
        )

    rental_ask = ""
    if ranked_names:
        area_list = ", ".join(ranked_names)
        rental_ask = f"""
7. After the narrative, add a line "###AREA_ESTIMATES###" followed by one line per
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

Per-area reliability summary (already computed from the raw data below):
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
4. A recommendation for the exact time the user should leave home to arrive by {arrive_office or 'their target time'}.
5. Keep the report concise and practical.
6. Ignore any travel times that appear unrealistic or above 120 minutes.
{rental_ask}
Raw data:
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


def send_report_email(to_email: str, office_name: str, report: str, tracking_request_id: str) -> bool:
    """Email the finished report via Resend. No-op (returns False) if unconfigured."""
    if not RESEND_API_KEY:
        print("RESEND_API_KEY not set — skipping email (report still saved to DB).")
        return False

    report_url = f"{APP_BASE_URL}/report/{tracking_request_id}" if APP_BASE_URL else None
    safe_report = html.escape(report)
    link_html = f"<p><a href='{report_url}'>View the full report online</a></p>" if report_url else ""
    body_html = (
        "<div style=\"font-family:Segoe UI,Arial,sans-serif;color:#16324f;max-width:640px\">"
        "<h2 style=\"color:#0f4c92\">Your KKM relocation report</h2>"
        f"<p>Target office: <strong>{html.escape(office_name)}</strong></p>"
        "<pre style=\"white-space:pre-wrap;background:#f7fbff;border:1px solid #dce9f6;"
        f"border-radius:12px;padding:16px;line-height:1.6\">{safe_report}</pre>"
        f"{link_html}"
        "<p style=\"color:#60748a;font-size:13px\">Sent by the KKM Relocation Planner (free tool for MOH staff).</p>"
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

    logs = fetch_tracking_logs(tracking_request_id)
    filtered_logs = filter_logs_for_analysis(logs)
    formatted_logs = format_logs_for_prompt(filtered_logs)
    anchor_summary, ranked_names = summarize_anchor_reliability(filtered_logs)
    prompt = build_prompt(
        tracking_request_id,
        formatted_logs,
        anchor_summary=anchor_summary,
        ranked_names=ranked_names,
        office_name=request_row.get("target_office_name") or "the target office",
        leave_home=(request_row.get("return_time") or "")[:5] or None,   # return_time = leave-home
        arrive_office=(request_row.get("arrival_time") or "")[:5] or None,
    )
    report = generate_report_with_groq(prompt)
    area_price_estimates = parse_area_estimates(report)

    save_report_to_file(report, tracking_request_id)
    save_report_to_db(report, tracking_request_id, area_price_estimates)

    email = request_row.get("user_email")
    if email:
        send_report_email(email, request_row.get("target_office_name") or "your target office", report, tracking_request_id)

    return report


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python generate_report.py <tracking_request_id>")
        sys.exit(1)

    result = process_request(sys.argv[1])
    print(result)
    print(f"\nDone for tracking_requests row {sys.argv[1]}")
