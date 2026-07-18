"""
Build data/klinik_kesihatan_official.json: the authoritative list of Klinik
Kesihatan scraped from the official MOH facility directory
(moh.gov.my/en/health-facilities/health-clinic/<state>), each flagged with
whether an equivalent already exists in our OSM-seeded `locations` table.

The clinics NOT in OSM (in_osm=false) are the gap to fill; each carries its
official full address so coordinates can be geocoded later.

Input: the browser tool-results dump path (arg 1).
Run:   python python_scripts/build_missing_kk.py "<tool-results .txt path>"
"""

import os
import re
import sys
import json
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

load_dotenv(Path(__file__).resolve().parents[1] / ".env.local")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
sb = create_client(os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

DUMP = sys.argv[1]


def load_official(path):
    raw = Path(path).read_text(encoding="utf-8")
    env = json.loads(raw)
    # env is [{type,text}, ...]; element 0 holds the JS return (the data).
    # It is JSON-string-encoded and may have trailing junk, so peel layers
    # with raw_decode (which ignores anything after the first JSON value).
    dec = json.JSONDecoder()
    val = env[0]["text"] if isinstance(env, list) else raw
    for _ in range(3):
        if isinstance(val, dict):
            return val
        val, _end = dec.raw_decode(val.lstrip())
    raise ValueError("could not decode official data")


PREFIX = re.compile(
    r"\b(klinik kesihatan ibu dan anak|klinik kesihatan|klinik 1 ?malaysia|klinik desa|klinik|kesihatan|kkia|k1m|kk|kd)\b",
    re.IGNORECASE,
)


def core(name):
    s = PREFIX.sub(" ", name.lower())
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


def load_osm_cores():
    cores, start = {}, 0
    while True:
        rows = sb.table("locations").select("name, latitude, longitude").eq("tier", "clinic").range(start, start + 999).execute().data
        for r in rows:
            c = core(r["name"])
            if c:
                cores.setdefault(c, (r["latitude"], r["longitude"]))
        if len(rows) < 1000:
            break
        start += 1000
    return cores


def main():
    official = load_official(DUMP)
    osm = load_osm_cores()
    print(f"official states: {len(official)}  |  OSM clinic cores: {len(osm)}")

    out = {}
    total = matched = missing = 0
    for state, items in official.items():
        recs = []
        for it in items:
            name, addr = it["n"], it.get("a", "")
            c = core(name)
            in_osm = c in osm
            rec = {"name": name, "address": addr, "in_osm": in_osm}
            if in_osm:
                rec["osm_lat"], rec["osm_lng"] = osm[c]
                matched += 1
            else:
                missing += 1
            recs.append(rec)
            total += 1
        recs.sort(key=lambda r: r["name"])
        out[state] = recs

    payload = {
        "_meta": {
            "source": "https://www.moh.gov.my/en/health-facilities/health-clinic/<state>",
            "description": "Official MOH Klinik Kesihatan directory, flagged against OSM-seeded locations table.",
            "total_official_kk": total,
            "already_in_osm": matched,
            "missing_from_osm": missing,
            "note": "Matching is by normalized location-core name and is approximate. 'missing' rows carry the official address for geocoding.",
        },
        "states": out,
    }

    out_path = Path(__file__).resolve().parents[1] / "data" / "klinik_kesihatan_official.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Also a slim missing-only file (what needs geocoding).
    missing_only = {s: [r for r in recs if not r["in_osm"]] for s, recs in out.items()}
    missing_only = {s: v for s, v in missing_only.items() if v}
    (out_path.parent / "klinik_kesihatan_missing.json").write_text(
        json.dumps({"_meta": payload["_meta"], "states": missing_only}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"total={total}  in_osm={matched}  missing={missing}")
    print("per-state missing:", {s: len([r for r in recs if not r['in_osm']]) for s, recs in out.items()})
    print("wrote:", out_path)


if __name__ == "__main__":
    main()
