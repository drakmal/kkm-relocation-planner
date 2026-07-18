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

_~3,100 facilities total. KK now meets the official benchmark; KD greatly
expanded via the per-state official directory (Perlis and Perak reconciled
exactly against their MOH directories)._

| Tier | Count | Breakdown |
| --- | --- | --- |
| `state` (JKN) | 16 | 16 |
| `district` | ~465 | ~140 hospital · 162 PKD · 162 PKPD |
| `clinic` | ~2,640 | ~1,124 KK · ~959 KKP · ~495 KD · gov clinic · K1M |

Coordinates: after two geocoding passes (place + postcode), only ~61 facilities
remain on approximate (state-centre) coordinates; the rest are town-level or
exact.

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
| `refine_coords.py` / `refine_coords_v2.py` | Re-geocode approximate rows (place then postcode strategy) |
| `reconcile_perlis.py` / `add_perak_facilities.py` | Per-state reconciliation against the official MOH directory |
| `find_anchors.py` | Per tracking request: OSM residential anchors around the target office |
| `collect_traffic.py` | GitHub Actions (every 15 min, morning window): collects per request ~near its leave-home time; skips weekends + public holidays |
| `run_due_reports.py` | GitHub Actions (hourly): generate + email reports whose tracking window has ended |
| `generate_report.py` | Groq (Llama) analysis → writes report to DB + emails via Resend |

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

---

## Deployment & operations

- **Live site:** https://kkm-relocation-planner.vercel.app (Vercel, auto-deploys on push to `main`).
- **Repo:** https://github.com/drakmal/kkm-relocation-planner (public).
- **Database:** Supabase (cloud). A `keep_alive.yml` workflow pings it every 3 days so the free tier never pauses.
- **Scheduled jobs (GitHub Actions):**
  - `daily_tracker.yml` — commute-timed traffic collection (every 15 min, 05:00-10:00 MYT).
  - `generate_reports.yml` — hourly report generation + email.
  - `keep_alive.yml` — Supabase keep-alive.

### Environment variables

| Variable | Vercel (web app) | GitHub Actions (python) |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` / `_ANON_KEY` | ✅ | — |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | ✅ | ✅ |
| `GOOGLE_MAPS_API_KEY` | ✅ | ✅ (Distance Matrix) |
| `GROQ_API_KEY` | — | ✅ (report LLM) |
| `RESEND_API_KEY` / `RESEND_FROM` | ✅ (confirmation email) | ✅ (report email) |
| `APP_BASE_URL` | ✅ | ✅ (report links) |

**Notes:**
- Confirmation email + report email need `RESEND_*` in **both** places (the web app sends the confirmation; the Actions send the report). `RESEND_FROM` must be a verified Resend sending domain — `onboarding@resend.dev` only delivers to the account owner.
- Google Distance Matrix is the only paid dependency (traffic-aware). Cost is bounded by: 3 requests/email/week, ≤5 working days, and the per-request anchor count. See the app's Limitations card.
- The submit route calls Overpass inline (~10-15s). If it ever times out on the serverless host, move anchor creation into a background Action.
