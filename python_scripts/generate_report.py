import os
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
    Fetch all daily traffic logs for a specific tracking request.
    """
    if not supabase:
        raise RuntimeError("Supabase client not initialized")

    response = (
        supabase.table("daily_traffic_logs")
        .select("log_date, travel_time_minutes, weather_temp_c, weather_condition, weather_wind_kmh, weather_precip_mm, origin_anchors(name)")
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
                max_tokens=400,
            )
            return response.choices[0].message.content
        except Exception as exc:
            if attempt < 2 and "429" in str(exc):
                time.sleep(10)
                continue
            raise RuntimeError(f"Groq API request failed: {exc}")

    raise RuntimeError("Groq API request failed after retries.")


def build_prompt(tracking_request_id: str, formatted_logs: str):
    return f"""
You are a transport analyst for Malaysian health workers.
Analyze the following commute tracking data for tracking request {tracking_request_id}.

Use the travel times and weather conditions to identify which residential areas are most reliable for travel to the target office.
Factor in weather delays from wind, rain, and temperature where relevant.

Return a clean text report with:
1. A short summary of the overall pattern.
2. The top 3 residential areas ranked by reliability.
3. A recommendation for the exact time the user should leave home for the morning commute.
4. Keep the report concise and practical.
5. Ignore any travel times that appear unrealistic or above 120 minutes.

Data:
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


def save_report_to_db(report: str, tracking_request_id: str) -> None:
    """
    Persist the report back onto the tracking_requests row so the web app can
    display it. Without this, the report only exists as a local file and is
    invisible to users (and lost when run on an ephemeral CI runner).
    """
    if not supabase:
        raise RuntimeError("Supabase client not initialized")

    supabase.table("tracking_requests").update(
        {
            "report_text": report,
            "report_generated_at": datetime.now(timezone.utc).isoformat(),
            # Must match the tracking_requests.status check constraint
            # ('pending' | 'active' | 'completed' | 'cancelled').
            "status": "completed",
        }
    ).eq("id", tracking_request_id).execute()


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
        .select("user_email, target_office_name")
        .eq("id", tracking_request_id)
        .maybe_single()
        .execute()
        .data
    ) or {}

    logs = fetch_tracking_logs(tracking_request_id)
    filtered_logs = filter_logs_for_analysis(logs)
    formatted_logs = format_logs_for_prompt(filtered_logs)
    prompt = build_prompt(tracking_request_id, formatted_logs)
    report = generate_report_with_groq(prompt)

    save_report_to_file(report, tracking_request_id)
    save_report_to_db(report, tracking_request_id)

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
