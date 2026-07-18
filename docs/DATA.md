# KKM Relocation Planner — Facility Data

This document describes the health-facility dataset behind the target-office
selector (the `locations` table) — where it comes from, how it is structured,
how to regenerate it, and its known limitations.

> **Data-integrity principle:** every facility name and coordinate in this
> dataset comes from a real source (official MOH directory or OpenStreetMap).
> Nothing is invented. Where a precise coordinate could not be obtained, the row
> is flagged (`metadata.source = "approx_*"`) rather than given a fake location.

---

## Facility hierarchy (the form's 5 boxes)

```
Box 1  JKN <State>                        tier=state     (16 state/territory health departments)
Box 2  Hospital / PKD / PKPD              tier=district
         ├─ Hospital <name>               category=hospital     (parent: JKN)
         ├─ PKD <district>                category=pkd          (parent: JKN)  Pejabat Kesihatan Daerah
         └─ PKPD <district>               category=pkpd         (parent: JKN)  Pejabat Kesihatan Pergigian Daerah
Box 3  KK / KKP                           tier=clinic
         ├─ Klinik Kesihatan <name>       category=klinik_kesihatan   (parent: PKD)
         ├─ Klinik Pergigian <name>       category=klinik_pergigian   (parent: PKPD)  = KKP
         ├─ Klinik 1Malaysia / others     category=klinik_1malaysia | government_clinic
Box 4  Klinik Desa                        tier=clinic  category=klinik_desa   (parent: PKD)
Box 5  Others (MOH HQ, NIH)               static in the API (not stored)
```

`metadata.category` is the source of truth for which box a facility falls into;
the `/api/locations` route derives it (falling back to name/tier heuristics).

---

## Data sources

| Layer | Source | Coordinates from |
| --- | --- | --- |
| JKN (state depts) | Hand-curated (16 real state/territory health departments) | State-capital coordinates |
| PKD districts | OSM Overpass `admin_level=6` district boundaries | District centroid |
| PKPD districts | Mirror of PKD (one dental office per district) | Same as PKD |
| Hospitals | **Official** MOH / Wikipedia government-hospital list | OSM Nominatim geocode |
| Klinik Kesihatan (KK) | OSM Overpass + **official** MOH directory (`moh.gov.my/en/health-facilities/health-clinic/<state>`) | OSM / Nominatim geocode of official address |
| Klinik Pergigian (KKP) | **Official** MOH dental directory (`.../dental-clinic/<state>`) | Nominatim geocode of official address |
| Klinik Desa (KD) | OSM Overpass (`operator = Ministry of Health`) | OSM |

**Geocoding uses OSM Nominatim, not Google** — the project's Google API key has
Distance Matrix enabled but *not* Geocoding. See the note in `.env`.

The official MOH directory blocks non-browser fetches (WAF 403); it is scraped
via the in-app browser using same-origin `fetch()`.

---

## Current counts

_As of the latest seed (KKP and the 658 missing KK are still being added, so
clinic totals continue to grow toward the official benchmarks)._

| Tier | Count | Breakdown |
| --- | --- | --- |
| `state` (JKN) | 16 | 16 |
| `district` | 467 | 143 hospital · 162 PKD · 162 PKPD |
| `clinic` | 1,240+ | 469 KK · ~1,005 KKP · 311 KD · 46 gov clinic · 16 K1M |

### Official benchmarks (MOH Health Facts 2024, 2023 data)

| Facility | Official | Notes |
| --- | --- | --- |
| Government hospitals | 149 | We seed 143 (excludes university/military) |
| Klinik Kesihatan | ~1,095 | 997 in the official directory; being geocoded + seeded |
| Districts | ~159 | 162 PKD from OSM |

---

## Seed / build scripts (`python_scripts/`)

| Script | Purpose |
| --- | --- |
| `seed_facilities.py` | Base seed: JKN + PKD + hospitals + KK from OSM Overpass (per state) |
| `repair_facilities.py` | Re-fetch states whose facility query timed out during the base seed |
| `seed_hospitals_official.py` | Replace noisy OSM hospitals with the authoritative government-hospital list (geocoded) |
| `seed_klinik_desa.py` | Add real Klinik Desa (operator=MOH) additively |
| `seed_pkpd.py` | Seed PKPD (district dental offices), mirroring PKD |
| `seed_kkp.py` | Geocode + seed KKP (dental clinics) from the official dental directory dump |
| `build_missing_kk.py` | Diff official KK directory vs OSM → `data/klinik_kesihatan_*.json` |
| `seed_missing_kk.py` | Geocode + seed the KK the directory has but OSM was missing |
| `find_anchors.py` | Per tracking request: OSM residential anchors around the target office |
| `collect_traffic.py` | Daily (GitHub Actions): Google Distance Matrix + Open-Meteo per anchor |
| `generate_report.py` | Groq (Llama) analysis → writes report back to `tracking_requests` |

---

## Data files (`data/`)

| File | Contents |
| --- | --- |
| `klinik_kesihatan_official.json` | All 997 official KK, each flagged `in_osm`, with address |
| `klinik_kesihatan_missing.json` | The ~658 official KK not yet in OSM, with addresses for geocoding |

---

## Known limitations

- **KK completeness:** OSM alone covered ~43% of official KK. The official MOH
  directory closes this (997 listed); the 658 missing are geocoded from their
  official addresses via Nominatim. A small number may fall back to
  approximate (district/state) coordinates where the address does not resolve.
- **PKPD coordinates** reuse the PKD (district) coordinate — the dental office
  is in the same district town but not the exact same building.
- **Name-based matching** (official vs OSM) is approximate, so the "missing"
  count is a slight over-estimate where naming differs.
- The only route to a fully authoritative geocoded dataset is the
  access-restricted **MyHDW** directory (`support.myhdw@mimos.my`).
