"""
Replace the noisy OSM-derived hospital layer (Box 2) with an AUTHORITATIVE list
of Malaysian government hospitals (source: MOH / Wikipedia list of hospitals),
geocoded via OpenStreetMap Nominatim (free, no API key).

Steps:
  1. Reclassify any Klinik Kesihatan that were mis-filed under tier='district'
     into tier='clinic' (Box 3), parented to their nearest PKD.
  2. Delete the remaining non-PKD district rows (OSM hospitals + private + junk).
  3. Geocode each official government hospital and insert it (tier='district',
     parent = its state JKN).

PKD district offices (tier='district', name like 'PKD %') and the Klinik
Kesihatan layer are preserved.

Run:  python python_scripts/seed_hospitals_official.py
"""

import os
import re
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
            k, v = line.split("=", 1)
            if k.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip().strip("\"'")


load_env_file()
supabase = create_client(
    os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
)
PARENT_COLUMN = "parent_location_id"
NOMINATIM = "https://nominatim.openstreetmap.org/search"

# Authoritative government hospitals by JKN state (MOH / Wikipedia). University,
# military, and private hospitals intentionally excluded; parentheticals dropped.
HOSPITALS = {
    "JKN Johor": ["Hospital Sultanah Fatimah Muar", "Hospital Sultanah Nora Ismail Batu Pahat", "Hospital Enche Besar Hajjah Kalsom Kluang", "Hospital Segamat", "Hospital Pontian", "Hospital Kota Tinggi", "Hospital Mersing", "Hospital Tangkak", "Hospital Temenggong Seri Maharaja Tun Ibrahim Kulai", "Hospital Permai Johor Bahru", "Hospital Sultan Ismail Johor Bahru", "Hospital Sultanah Aminah Johor Bahru"],
    "JKN Kedah": ["Hospital Sultanah Bahiyah Alor Setar", "Hospital Sultan Abdul Halim Sungai Petani", "Hospital Pendang", "Hospital Kulim", "Hospital Baling", "Hospital Sik", "Hospital Sultanah Maliha Langkawi", "Hospital Yan", "Hospital Jitra", "Hospital Kuala Nerang"],
    "JKN Kelantan": ["Hospital Raja Perempuan Zainab II Kota Bharu", "Hospital Pasir Mas", "Hospital Tumpat", "Hospital Machang", "Hospital Jeli", "Hospital Tanah Merah", "Hospital Tengku Anis Pasir Puteh", "Hospital Gua Musang", "Hospital Kuala Krai", "Hospital Bachok"],
    "JKN WP Kuala Lumpur": ["Hospital Kuala Lumpur", "Hospital Rehabilitasi Cheras", "Hospital Tunku Azizah Kuala Lumpur"],
    "JKN WP Labuan": ["Hospital Nukleus Labuan", "Hospital Labuan"],
    "JKN Melaka": ["Hospital Melaka", "Hospital Alor Gajah", "Hospital Jasin"],
    "JKN Negeri Sembilan": ["Hospital Tuanku Ja'afar Seremban", "Hospital Tuanku Ampuan Najihah Kuala Pilah", "Hospital Port Dickson", "Hospital Tampin", "Hospital Jelebu", "Hospital Jempol", "Hospital Rembau"],
    "JKN Pahang": ["Hospital Tengku Ampuan Afzan Kuantan", "Hospital Pekan", "Hospital Kuala Lipis", "Hospital Raub", "Hospital Bentong", "Hospital Jerantut", "Hospital Jengka", "Hospital Muadzam Shah", "Hospital Sultan Haji Ahmad Shah Temerloh", "Hospital Cameron Highlands", "Hospital Rompin", "Hospital Bera"],
    "JKN Pulau Pinang": ["Hospital Pulau Pinang", "Hospital Sungai Bakap", "Hospital Bukit Mertajam", "Hospital Balik Pulau", "Hospital Seberang Jaya", "Hospital Kepala Batas"],
    "JKN Perak": ["Hospital Batu Gajah", "Hospital Bahagia Ulu Kinta", "Hospital Changkat Melintang", "Hospital Gerik", "Hospital Kampar", "Hospital Kuala Kangsar", "Hospital Parit Buntar", "Hospital Raja Permaisuri Bainun Ipoh", "Hospital Selama", "Hospital Seri Manjung", "Hospital Sungai Siput", "Hospital Slim River", "Hospital Tapah", "Hospital Taiping", "Hospital Teluk Intan"],
    "JKN Perlis": ["Hospital Tuanku Fauziah Kangar"],
    "JKN WP Putrajaya": ["Hospital Putrajaya", "Institut Kanser Negara Putrajaya"],
    "JKN Sabah": ["Hospital Queen Elizabeth Kota Kinabalu", "Hospital Duchess of Kent Sandakan", "Hospital Beaufort", "Hospital Beluran", "Hospital Keningau", "Hospital Kinabatangan", "Hospital Kota Belud", "Hospital Kota Marudu", "Hospital Kuala Penyu", "Hospital Kudat", "Hospital Kunak", "Hospital Lahad Datu", "Hospital Mesra Bukit Padang", "Hospital Papar", "Hospital Ranau", "Hospital Semporna", "Hospital Sipitang", "Hospital Tambunan", "Hospital Tawau", "Hospital Tenom", "Hospital Wanita dan Kanak-Kanak Sabah Likas"],
    "JKN Sarawak": ["Hospital Umum Sarawak Kuching", "Hospital Bau", "Hospital Sibu", "Hospital Miri", "Hospital Sarikei", "Hospital Sri Aman", "Hospital Limbang", "Hospital Lawas", "Hospital Kapit", "Hospital Sentosa Kuching", "Hospital Mukah", "Hospital Marudi", "Hospital Lundu", "Hospital Bintulu", "Hospital Betong", "Hospital Serian", "Hospital Simunjan", "Hospital Kanowit", "Hospital Saratok", "Hospital Dalat"],
    "JKN Selangor": ["Hospital Shah Alam", "Hospital Ampang", "Hospital Banting", "Hospital Tengku Permaisuri Norashikin Kajang", "Hospital Kuala Kubu Bharu", "Hospital Selayang", "Hospital Serdang", "Hospital Sungai Buloh", "Hospital Tanjung Karang", "Hospital Tengku Ampuan Jemaah Sabak Bernam", "Hospital Tengku Ampuan Rahimah Klang", "Hospital Al-Sultan Abdullah Puncak Alam", "Hospital Cyberjaya"],
    "JKN Terengganu": ["Hospital Sultanah Nur Zahirah Kuala Terengganu", "Hospital Dungun", "Hospital Kemaman", "Hospital Besut", "Hospital Hulu Terengganu", "Hospital Setiu"],
}

STATE_HINT = {
    "JKN Johor": "Johor", "JKN Kedah": "Kedah", "JKN Kelantan": "Kelantan",
    "JKN WP Kuala Lumpur": "Kuala Lumpur", "JKN WP Labuan": "Labuan",
    "JKN Melaka": "Melaka", "JKN Negeri Sembilan": "Negeri Sembilan",
    "JKN Pahang": "Pahang", "JKN Pulau Pinang": "Pulau Pinang", "JKN Perak": "Perak",
    "JKN Perlis": "Perlis", "JKN WP Putrajaya": "Putrajaya", "JKN Sabah": "Sabah",
    "JKN Sarawak": "Sarawak", "JKN Selangor": "Selangor", "JKN Terengganu": "Terengganu",
}


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def geocode(name, state):
    """Nominatim geocode. Returns (lat, lng) or None. Respects 1 req/sec."""
    headers = {"User-Agent": "KKM-Relocation-Planner/1.0 (facility seeding)"}
    for q in (f"{name}, {state}, Malaysia", f"{name}, Malaysia"):
        try:
            r = requests.get(
                NOMINATIM,
                params={"q": q, "format": "json", "limit": 1, "countrycodes": "my"},
                headers=headers, timeout=30,
            )
            time.sleep(1.1)
            if r.status_code == 200 and r.json():
                hit = r.json()[0]
                return round(float(hit["lat"]), 6), round(float(hit["lon"]), 6)
        except requests.RequestException:
            time.sleep(2)
    return None


def get_jkn_map():
    rows = supabase.table("locations").select("id, name, latitude, longitude").eq("tier", "state").execute().data or []
    return {r["name"]: r for r in rows}


def get_pkd_points(jkn_id):
    rows = (
        supabase.table("locations").select("id, latitude, longitude")
        .eq(PARENT_COLUMN, jkn_id).eq("tier", "district").like("name", "PKD %").execute().data or []
    )
    return [{"id": r["id"], "lat": r["latitude"], "lng": r["longitude"]} for r in rows]


def rescue_misfiled_kk(jkn_map):
    """Move Klinik Kesihatan mistakenly stored under tier='district' into Box 3."""
    moved = 0
    dist = supabase.table("locations").select("id, name, latitude, longitude, " + PARENT_COLUMN).eq("tier", "district").execute().data or []
    # Build per-JKN PKD lookups lazily.
    pkd_cache = {}
    for row in dist:
        if "klinik kesihatan" not in row["name"].lower():
            continue
        jkn_id = row[PARENT_COLUMN]
        if jkn_id not in pkd_cache:
            pkd_cache[jkn_id] = get_pkd_points(jkn_id)
        pkds = pkd_cache.get(jkn_id) or []
        parent = min(pkds, key=lambda p: haversine_km(row["latitude"], row["longitude"], p["lat"], p["lng"]))["id"] if pkds else jkn_id
        supabase.table("locations").update({"tier": "clinic", PARENT_COLUMN: parent}).eq("id", row["id"]).execute()
        moved += 1
    return moved


def delete_non_pkd_districts():
    """Delete every district-tier row that is not a clean 'PKD ...' office."""
    rows = supabase.table("locations").select("id, name").eq("tier", "district").execute().data or []
    ids = [r["id"] for r in rows if not r["name"].lower().startswith("pkd ")]
    for i in range(0, len(ids), 100):
        supabase.table("locations").delete().in_("id", ids[i:i + 100]).execute()
    return len(ids)


def main():
    jkn_map = get_jkn_map()

    print("Rescuing mis-filed Klinik Kesihatan from Box 2...", flush=True)
    moved = rescue_misfiled_kk(jkn_map)
    print(f"  moved {moved} KK -> Box 3", flush=True)

    print("Deleting noisy OSM hospital/junk rows...", flush=True)
    deleted = delete_non_pkd_districts()
    print(f"  deleted {deleted} non-PKD district rows", flush=True)

    inserted, approx = 0, 0
    for jkn_name, hospitals in HOSPITALS.items():
        jkn = jkn_map.get(jkn_name)
        if not jkn:
            print(f"! {jkn_name} not found, skipping", flush=True)
            continue
        state = STATE_HINT[jkn_name]
        rows = []
        for name in hospitals:
            coord = geocode(name, state)
            if coord:
                lat, lng = coord
                meta = {"kind": "hospital", "source": "official+nominatim"}
            else:
                lat, lng = jkn["latitude"], jkn["longitude"]  # rough fallback
                meta = {"kind": "hospital", "source": "official", "geocode": "approx_state"}
                approx += 1
            rows.append({
                "name": name, "tier": "district", PARENT_COLUMN: jkn["id"],
                "latitude": lat, "longitude": lng, "address": state, "metadata": meta,
            })
        if rows:
            supabase.table("locations").insert(rows).execute()
            inserted += len(rows)
        print(f"  {jkn_name}: +{len(rows)} hospitals", flush=True)

    print(f"\n=== DONE ===  inserted={inserted}  (approx_coord={approx})", flush=True)


if __name__ == "__main__":
    main()
