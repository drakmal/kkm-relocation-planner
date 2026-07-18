import os
from pathlib import Path
from supabase import create_client


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

SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Please set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in your environment.")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def build_location_payload(row, parent_column):
    payload = {
        "name": row["name"],
        "tier": row["tier"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "address": row["address"],
        "metadata": row["metadata"],
    }
    if parent_column == "parent_id":
        payload["parent_id"] = row.get("parent_id")
    else:
        payload["parent_location_id"] = row.get("parent_id")
    return payload


def insert_locations(rows):
    try:
        payloads = [build_location_payload(row, "parent_id") for row in rows]
        return supabase.table("locations").insert(payloads).execute()
    except Exception as exc:
        message = str(exc)
        if "parent_id" in message and "Could not find" in message:
            payloads = [build_location_payload(row, "parent_location_id") for row in rows]
            return supabase.table("locations").insert(payloads).execute()
        raise


def update_parent(id_value, parent_id, parent_column):
    payload = {parent_column: parent_id}
    return supabase.table("locations").update(payload).eq("id", id_value).execute()


rows = [
    {
        "name": "JKN Perak",
        "tier": "ministry",
        "parent_id": None,
        "latitude": 4.5975,
        "longitude": 101.0901,
        "address": "Perak, Malaysia",
        "metadata": {"code": "jkn-perak"},
    },
    {
        "name": "Hospital Tapah",
        "tier": "district",
        "parent_id": None,
        "latitude": 4.2000,
        "longitude": 101.2667,
        "address": "Tapah, Perak",
        "metadata": {"code": "hospital-tapah"},
    },
    {
        "name": "PKD Batang Padang",
        "tier": "state",
        "parent_id": None,
        "latitude": 4.0000,
        "longitude": 101.3000,
        "address": "Batang Padang, Perak",
        "metadata": {"code": "pkd-batang-padang"},
    },
    {
        "name": "KK Tapah",
        "tier": "clinic",
        "parent_id": None,
        "latitude": 4.2000,
        "longitude": 101.2667,
        "address": "Tapah, Perak",
        "metadata": {"code": "kk-tapah"},
    },
]

result = insert_locations(rows)
print("Inserted mock locations:", result)

created_rows = getattr(result, "data", None) or []
name_to_id = {item["name"]: item["id"] for item in created_rows}

parent_column = "parent_id"
if not created_rows:
    created = supabase.table("locations").select("id,name").in_("name", ["JKN Perak", "Hospital Tapah", "PKD Batang Padang", "KK Tapah"]).execute()
    created_rows = getattr(created, "data", None) or []
    name_to_id = {item["name"]: item["id"] for item in created_rows}

if created_rows and "parent_id" not in created_rows[0]:
    parent_column = "parent_location_id"

updates = []
if name_to_id.get("Hospital Tapah"):
    updates.append({"id": name_to_id["Hospital Tapah"], "parent_id": name_to_id.get("JKN Perak")})
if name_to_id.get("PKD Batang Padang"):
    updates.append({"id": name_to_id["PKD Batang Padang"], "parent_id": name_to_id.get("JKN Perak")})
if name_to_id.get("KK Tapah"):
    updates.append({"id": name_to_id["KK Tapah"], "parent_id": name_to_id.get("PKD Batang Padang")})

for update in updates:
    update_parent(update["id"], update["parent_id"], parent_column)

print("Seeded location hierarchy complete.")
