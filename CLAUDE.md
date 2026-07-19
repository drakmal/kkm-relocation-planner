# KKM Relocation Planner — Project Context

Read this first. It is the handoff/state doc; `docs/DATA.md` has full facility-data detail.

## What this is
A free web tool for Malaysian Ministry of Health (MOH) staff to evaluate commute
reliability (real traffic + weather) to a target health facility over a few
working days, then receive a report recommending the best residential areas to
live within commuting range — with rental-listing links. **Independent project,
not an official MOH commission.**

## Live
- **App:** https://kkm-relocation-planner.vercel.app  (Vercel, auto-deploys on push to `main`)
- **Repo:** https://github.com/drakmal/kkm-relocation-planner  (public; owner `drakmal`, `gh` CLI is authed)
- **DB:** Supabase (cloud)

## Tech stack
- Frontend: **Next.js 14 App Router**, hand-rolled utility CSS + Tailwind
- Backend/DB: **Supabase** (Postgres)
- Data collection + reports: **Python** run in **GitHub Actions**
- Traffic: **Google Distance Matrix** (paid — the main cost) + **Open-Meteo** weather
- Geocoding: **OSM Nominatim** (⚠️ Google Geocoding is NOT enabled on the key)
- Report AI: **Groq** (Llama 3.3)
- Email: **Resend** — currently **dormant** (no verified sending domain). App uses an
  on-screen **reference ID + `/report/[id]` link** instead (Option B). Email code is
  left in place best-effort; it auto-activates if a domain is verified later.

## Run locally
- Dev: `npm run dev`  (⚠️ do NOT run `next build` while dev is running — it corrupts `.next`; stop dev first, or `rm -rf .next` after)
- Prod build check: `npm run build`
- Python: `python python_scripts/<name>.py` (auto-loads `.env` + `.env.local`)

## Key files
- `app/page.tsx` — main form: 5-box facility cascade, OSM map of selected target, date-range tracking period, weekly usage counter, limit-reached modal
- `app/report/[id]/page.tsx` — status/report page + rental links
- `app/api/locations/route.ts` — dropdown data (server-side parent filtering; returns `category` + coords)
- `app/api/requests/route.ts` — POST submit + GET weekly usage; MOH email gate + 3/week limit + confirmation email (best-effort)
- `app/api/feedback/route.ts` — POST "can't find my facility" / issue reports -> Supabase `feedback` table (admin reads in Supabase dashboard); light IP/day spam cap
- `app/lib/` — `originAnchors` (anchor discovery), `holidays`, `rental`, `email`
- `python_scripts/` — seeders (`seed_*`, `add_perak_facilities`, `add_penang_facilities`, `add_state_facilities` (generic KK reconciler — `"<state key>" [--dry-run]`), `add_kd_from_osm` (best-effort KD top-up from OSM), `add_mukah_sarawak_facilities`, `add_sabah_facilities` (Sabah SKN directory — hospitals+KK+KD), `reconcile_perlis`, `refine_coords*`), `collect_traffic`, `run_due_reports`, `generate_report`, `find_anchors`, `run_pending_anchors` (background anchor builder)
- `data/` — `public_holidays_my.json`, `klinik_kesihatan_official.json`, `klinik_kesihatan_missing.json`, `sabah_health_facilities.json` (SKN scrape), `mukah_sarawak_facilities.xlsx`
- `.github/workflows/` — `daily_tracker.yml` (collection), `create_anchors.yml` (anchor builder), `generate_reports.yml`, `keep_alive.yml`

## Facility data (Supabase `locations`, ~3,381 rows)
5-box hierarchy, driven by `metadata.category`:
- Box 1 JKN (tier `state`) → Box 2 Hospital/PKD/PKPD (tier `district`) → Box 3 KK/KKP (tier `clinic`)
- Box 4 Klinik Desa (tier `clinic`, `klinik_desa`) · Box 5 Others (static MOH HQ, NIH, + Hospital KL as ministry root)
Sources: official MOH directories (hospitals, KK, KKP) + OSM Overpass, geocoded via Nominatim. See `docs/DATA.md`.

## Cost model & guardrails
Google Distance Matrix is the **only paid dependency** (~$10/1,000 traffic-aware calls; ~$200/mo free credit ≈ 20k calls). Guardrails already built:
MOH `@moh.gov.my` email gate · **3 requests/email/week** · **1–5 working days** · skip weekends + MY public holidays · ~120 anchors/request (Overpass, 25 km radius).

## Environment variables
| Var | Vercel (web app) | GitHub Actions (python) |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` / `_ANON_KEY` | ✅ | — |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | ✅ | ✅ |
| `GOOGLE_MAPS_API_KEY` | ✅ | ✅ |
| `GROQ_API_KEY` | — | ✅ |
| `RESEND_API_KEY` / `RESEND_FROM` / `APP_BASE_URL` | ✅ (dormant) | ✅ (dormant) |
| `NEXT_PUBLIC_TURNSTILE_SITE_KEY` | ⬜ needs Cloudflare Turnstile keys | — |
| `TURNSTILE_SECRET_KEY` | ⬜ needs Cloudflare Turnstile keys | — |

GitHub secrets are all set (`gh secret list`). Vercel env is managed in its dashboard.

**Turnstile CAPTCHA is coded but inert until keys are set.** Create a free Turnstile
widget at Cloudflare (dash.cloudflare.com → Turnstile), then add both vars in Vercel.
Client renders the widget only when `NEXT_PUBLIC_TURNSTILE_SITE_KEY` is present; server
verifies only when `TURNSTILE_SECRET_KEY` is present — so missing keys = no CAPTCHA,
never a broken form.

## Scheduled jobs (GitHub Actions — all verified running)
- `daily_tracker.yml` — `collect_traffic.py` every 15 min in 05:00–10:00 MYT; collects per request near its leave-home time; skips weekends/holidays/off-window; once/day dedup.
- `create_anchors.yml` — `run_pending_anchors.py` every 10 min; builds Overpass anchors for any non-completed request that has none (moved off the submit path).
- `generate_reports.yml` — `run_due_reports.py` hourly; generates + (dormant) emails reports whose window ended.
- `keep_alive.yml` — pings Supabase every 3 days (prevents free-tier pause).

## STATUS: deployed & verified end-to-end (reads, submit+anchors, MOH gate, all 3 Actions succeed).

## Pending / next steps (priority order)
0. **⏰ [REMINDER for owner] Shorten the app URL.** Owner asked to do this *later* — surface it. Canonical URL today is `kkm-relocation-planner.vercel.app` (already short + public + Turnstile-allowlisted; do NOT share the `-git-main-…vercel.app` URL — it's login-gated + not Turnstile-allowlisted). To shorten further: rename the Vercel project (→ e.g. `kkm-planner.vercel.app`) then update the Turnstile hostname allowlist + `APP_BASE_URL`; or add a custom domain (needs a purchase — currently deferred).
1. **[Security, HIGH]** Email is only *format*-checked — a fake `@moh.gov.my` passes and each fake email gets its own 3/week quota → can run up Google cost.
   - ✅ **IP-based rate limit implemented** (`app/api/requests/route.ts`, `IP_WEEKLY_LIMIT = 5`). Best-effort: `ip_address` is written via a post-insert update and the gate is skipped if the column is missing, so it deploys safely before migration. **ACTION REQUIRED:** run `db/003_add_ip_rate_limit.sql` in the Supabase SQL editor to add the `ip_address` column + index; the cap is inert until then.
   - ✅ **IP cap = 5/IP/week** (`IP_WEEKLY_LIMIT = 5`) — a bit above the 3/email cap so a shared office (several colleagues behind one NAT) isn't blocked in normal use.
   - ✅ **Cloudflare Turnstile CAPTCHA implemented** (client widget in `app/page.tsx`, server verify in `route.ts`). Gated on env keys — inert until `NEXT_PUBLIC_TURNSTILE_SITE_KEY` + `TURNSTILE_SECRET_KEY` are set in Vercel (see env table). **ACTION REQUIRED:** create a Turnstile widget at Cloudflare and add both keys.
   - ⬜ Still open: true email OTP (needs a sending domain).
2. ✅ **[Reliability] Done.** Anchor creation moved out of the submit path into a
   background Action. Submit now just saves the request; `create_anchors.yml`
   runs `run_pending_anchors.py` every 10 min to build anchors for any request
   that has none. Removes the ~14 s inline Overpass call (Vercel timeout risk).
3. **[Data]** ~61 facilities still on approximate (state-centre) coords (no usable postcode/OSM entry).
4. ✅ **[Data] KK reconciliation DONE for all 16 states** vs the official directory (`data/klinik_kesihatan_official.json`, already committed — **no scraping needed**; the MOH site's old scrape URL now 404s). Tool: **`add_state_facilities.py "<state key>" [--dry-run]`** — dedupes against the LIVE DB by a canonicalized name (handles Ayer/Air, Seri/Sri, Sg/Sungei, and KK/Klinik/KK1M/KD prefixes; excludes dental/lab rows), geocodes by postcode with a town fallback, parents to nearest PKD. Batch run added **14 KKs** across Kedah/Kelantan/Melaka/Sarawak/Selangor/Terengganu/WP KL/Pulau Pinang; the rest were already fully covered. Notes: `SKIP` set holds verified same-place duplicates the official list names without a locality suffix (e.g. Selangor "KK Batu 9" == "…Batu 9, Cheras"); one town-fallback miss (KK Paloh) was manually corrected to Belawai. **Klinik Desa (KD):** no official KD directory exists anywhere we can reach (MOH site gone; the committed JSON is KK-only), so KD can't be fully reconciled like KK. `add_kd_from_osm.py` does a best-effort OSM top-up (filters "Opposite …" markers + duplicate-mapped nodes, dedupes vs live DB, parents to nearest PKD) — it added ~13 named KD OSM had that we lacked. OSM's KD coverage is thinner than our DB, so the real gap (esp. Sabah/Sarawak, which have hundreds of KD in reality) remains **unfillable without an official KD source**. Perak's KD (243) is the only near-complete state, from an earlier manual scrape. **Sarawak — Bahagian Mukah** was filled from a user-supplied list (`data/mukah_sarawak_facilities.xlsx`, public Google-search info) via `add_mukah_sarawak_facilities.py`: added 1 hospital (Daro) + 9 clinics (KKIA Mukah/Dalat, KK1M Bandar/Sesok Mukah, KK Igan/Ladang Sawit/Nanga Semah/Betanak/Tekajong). Same pattern works for any other division/state list a user provides. **Sabah — filled statewide** from the Sabah SKN directory (`skn.sabah.gov.my/iknow/kesihatan-keselamatan/fasiliti-kesihatan` — server-rendered, NOT bot-blocked; curl works) via `add_sabah_facilities.py` reading `data/sabah_health_facilities.json` (353 scraped). Added **225** (6 hospitals + 164 KD + 55 KK/KKom), category by NAME prefix (source's type column mislabels KD as "Klinik Kesihatan"), parented by the district column. Sabah KD went 5 → 169. 20 rows that wouldn't geocode were snapped to their district PKD centroid (`source=pkd_district_centroid`). **This is the model for the remaining East-Malaysia gaps** — state health-dept directories, not moh.gov.my.
5. **[Feature, DEFERRED by owner]** Activate email: buy any domain (need NOT be moh.gov.my) → verify in Resend → set `RESEND_FROM` to it in Vercel + GitHub. Then confirmation + report emails auto-send. **Owner is not buying a domain for now** — revisit if another project needs one (then item #1's email-OTP option also unblocks). App runs fine on the reference-ID + `/report/[id]` link flow meanwhile.
6. **[Testing]** Full report pipeline not yet exercised with real collected traffic data (needs a real request run through a weekday collection cycle + `run_due_reports`).
7. ✅ **[Collection] Done.** Collection window widened to **05:00–12:00 MYT** (`daily_tracker.yml` cron `*/15 21-23,0-3 * * *`); with the 30-min lead this covers leave-home times up to ~12:15. Widen further only if users report leaving home after noon.
8. ✅ **[Feedback] Built.** "Can't find your facility?" form on `app/page.tsx` -> `app/api/feedback/route.ts` -> Supabase `feedback` table. Owner reads reports in the Supabase Table Editor (no email/domain needed). **ACTION REQUIRED:** run `db/004_add_feedback_table.sql` in the Supabase SQL editor — until then submissions 500 (the form renders fine either way). Light spam guard: 20 reports/IP/day (best-effort, self-disables if `ip_address` column absent).

## Gotchas
- Google Geocoding is disabled on the key → use **Nominatim** (postcode query works best for MY).
- `moh.gov.my` and `mudah.my` block bots (HTTP 403) → scrape via the in-app **browser** using same-origin `fetch()`.
- `next build` while `next dev` is running corrupts `.next` (clear it + restart dev).
- Facility geocoding on public Nominatim is rate-limited — run one geocoding job at a time.
