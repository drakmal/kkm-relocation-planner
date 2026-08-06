# KKM Relocation Planner — Project Context

Read this first. It is the handoff/state doc; `docs/DATA.md` has full facility-data detail.

## What this is
A free web tool for Malaysian Ministry of Health (MOH) staff to evaluate commute
reliability (real traffic + weather) to a target health facility over a few
working days, then receive a report recommending the best residential areas to
live within commuting range — with rental-listing links. **Independent project,
not an official MOH commission.**

## Live
- **App:** https://relocate.drakmal.com  (custom domain, on Vercel, auto-deploys on push to `main`)
  - Owner bought personal domain `drakmal.com` (Porkbun) and mapped this project to the `relocate` subdomain — kept generic/non-official-sounding on purpose (not `kkm.drakmal.com`) since this is an independent project, not an MOH commission. Root `drakmal.com` stays free for future projects/portfolio.
  - Old fallback URLs still exist but are not primary: `kkm-relocation-planner.vercel.app` (original Vercel URL). A short-lived `aml.autos` domain was bought and connected first, then disconnected once `drakmal.com` was acquired.
  - `APP_BASE_URL` updated to `https://relocate.drakmal.com` in both Vercel env vars and GitHub secrets.
- **Repo:** https://github.com/drakmal/kkm-relocation-planner  (public; owner `drakmal`, `gh` CLI is authed)
- **DB:** Supabase (cloud)

## Tech stack
- Frontend: **Next.js 14 App Router**, hand-rolled utility CSS + Tailwind
- Backend/DB: **Supabase** (Postgres)
- Data collection + reports: **Python** run in **GitHub Actions**
- Traffic: **Google Distance Matrix** (paid — the main cost) + **Open-Meteo** weather
- Geocoding: **OSM Nominatim** (⚠️ Google Geocoding is NOT enabled on the key)
- Report AI: **Groq** (Llama 3.3)
- Email: **Resend**, on the `drakmal.com` domain (DKIM/SPF/DMARC records added at Porkbun,
  domain verified in Resend 2026-07-27). Confirmed live end-to-end that day via direct
  Resend API test sends. Submission now goes through a **hard-fail magic-link flow**:
  `POST /api/requests` creates a `pending_verifications` row and emails a link; only
  clicking it (`POST /api/verify/[token]`) creates the real `tracking_requests` row and
  spends quota. If the verification email can't be sent, the submission is rejected outright
  — no reference-ID fallback (see item 1).

## Run locally
- Dev: `npm run dev`  (⚠️ do NOT run `next build` while dev is running — it corrupts `.next`; stop dev first, or `rm -rf .next` after)
- Prod build check: `npm run build`
- Python: `python python_scripts/<name>.py` (auto-loads `.env` + `.env.local`)

## Key files
- `app/page.tsx` — main form: 5-box facility cascade, OSM map of selected target, date-range tracking period, weekly usage counter, limit-reached modal. Submit button: "Request real-time traffic tracking".
- `app/report/[id]/page.tsx` — status/report page + rental links (no reference-ID card — not needed since report delivery is by email)
- `app/api/locations/route.ts` — dropdown data (server-side parent filtering; returns `category` + coords)
- `app/api/requests/route.ts` — POST submit + GET weekly usage; MOH email gate + 3/week limit + Turnstile CAPTCHA + IP cap. POST no longer writes `tracking_requests` directly — it creates a `pending_verifications` row and emails a magic link (see below).
- `app/api/verify/[token]/route.ts` — GET (safe, non-mutating peek — safe for email-scanner pre-fetch) + POST (the actual confirm: atomically claims the pending row, re-checks quota, creates the real `tracking_requests` row, sends the confirmation email, returns `reportReadyText`)
- `app/verify/[token]/page.tsx` — interstitial page a magic-link email lands on; shows a "Confirm your tracking request" button that fires the POST above (the email's own link/button never triggers the request itself — see Gotchas)
- `app/api/feedback/route.ts` — POST "can't find my facility" / issue reports -> Supabase `feedback` table (admin reads in Supabase dashboard); light IP/day spam cap
- `app/lib/` — `originAnchors` (anchor discovery), `holidays`, `rental`, `email` (`sendVerificationEmail` = magic link, sent by `api/requests`; `sendConfirmationEmail` = post-confirm email, sent by `api/verify/[token]` and by `generate_report.py`)
- `python_scripts/` — seeders (`seed_*`, `add_perak_facilities`, `add_penang_facilities`, `add_state_facilities` (generic KK reconciler — `"<state key>" [--dry-run]`), `add_kd_from_osm` (best-effort KD top-up from OSM), `add_mukah_sarawak_facilities`, `add_sabah_facilities` (Sabah SKN directory — hospitals+KK+KD), `reconcile_perlis`, `refine_coords*`), `collect_traffic`, `run_due_reports`, `generate_report`, `find_anchors`, `run_pending_anchors` (background anchor builder), `notify_feedback` (feedback -> Telegram), `cleanup_pending_verifications` (daily sweep of expired, unconfirmed magic-link rows)
- `data/` — `public_holidays_my.json`, `klinik_kesihatan_official.json`, `klinik_kesihatan_missing.json`, `sabah_health_facilities.json` (SKN scrape), `mukah_sarawak_facilities.xlsx`
- `.github/workflows/` — `daily_tracker.yml` (collection), `create_anchors.yml` (anchor builder), `generate_reports.yml`, `keep_alive.yml`, `notify_feedback.yml` (Telegram alerts), `cleanup_pending_verifications.yml` (daily, expired magic-link cleanup)

## Facility data (Supabase `locations`, ~3,400 rows)
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
| `RESEND_API_KEY` / `RESEND_FROM` / `APP_BASE_URL` | ✅ set + functionally verified 2026-07-27 | ✅ set |
| `NEXT_PUBLIC_TURNSTILE_SITE_KEY` | ✅ set + functionally verified 2026-07-27 | — |
| `TURNSTILE_SECRET_KEY` | ✅ set + functionally verified 2026-07-27 | — |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | — | ✅ set, `db/005` run — alerts live |

GitHub secrets are all set (`gh secret list`). Vercel env confirmed to have the same set (owner-confirmed 2026-07-27).

**Turnstile CAPTCHA is coded, keys are set, and it's confirmed working end-to-end**
(2026-07-27): the client bundle has the site key baked in and loads the widget script;
a direct POST to the live `/api/requests` with no token correctly 400s with a CAPTCHA
error, confirming server-side verification is active.

**Resend email is confirmed working end-to-end** (2026-07-27), after fixing three
separate issues found along the way — worth knowing if email trouble resurfaces:
1. Local `.env`'s `RESEND_FROM` had a copy-paste bug (`RESEND_FROM=RESEND_FROM=...`, still
   pointing at the `resend.dev` sandbox) and `APP_BASE_URL` was empty — fixed to
   `noreply@drakmal.com` / `https://relocate.drakmal.com` in both `.env` and Vercel.
2. `drakmal.com` had no Resend DNS records in Porkbun (DKIM/SPF/DMARC) — added, then
   verified in Resend's dashboard.
3. Even after that, sends kept 422ing with "The domain is invalid" for a few more
   minutes — turned out to be DNS/verification propagation lag on Resend's side, not a
   real config problem; retrying shortly after resolved it. Both the original
   "Onboarding" API key and a newer "relocate" (Full access) key work equally once the
   domain is verified — currently using the "relocate" key everywhere for a cleaner name.

## Scheduled jobs (GitHub Actions — all verified running)
- `daily_tracker.yml` — `collect_traffic.py` every 15 min in 05:00–10:00 MYT; collects per request near its leave-home time; skips weekends/holidays/off-window; once/day dedup.
- `create_anchors.yml` — `run_pending_anchors.py` every 10 min; builds Overpass anchors for any non-completed request that has none (moved off the submit path).
- `generate_reports.yml` — `run_due_reports.py` hourly; generates + emails reports whose window ended (email live, see item 5).
- `keep_alive.yml` — pings Supabase every 3 days (prevents free-tier pause).
- `notify_feedback.yml` — `notify_feedback.py` every 15 min; pushes NEW `feedback` rows to Telegram (only when there's un-notified feedback — silent otherwise; stamps `notified_at`). Live (see item 8).
- `cleanup_pending_verifications.yml` — `cleanup_pending_verifications.py` daily; deletes expired, unconfirmed magic-link rows (confirmed ones are deleted immediately on confirm instead).

## STATUS: deployed & verified end-to-end (reads, submit+anchors, MOH gate, all 3 Actions succeed,
magic-link email verification live, Turnstile + Resend functionally confirmed working).

## Pending / next steps (priority order)
-1. ✅ **[Security, CRITICAL] Fixed 2026-07-29 — RLS enabled on all public tables.** Supabase security-alert email received 2026-07-29 (`rls_disabled_in_public`): every table (`users`, `locations`, `tracking_requests`, `origin_anchors`, `daily_traffic_logs`, `analysis_reports`, `feedback`, `pending_verifications`) had Row-Level Security **off** — `sql/supabase_schema.sql` only ever left it as a commented-out placeholder, never applied in db/002–008. Impact was that the anon/public Supabase API key could read/edit/delete every row in every table directly via the REST API, bypassing all app-layer guardrails. Fix: `db/009_enable_rls.sql` (RLS on, zero policies — safe because every DB write goes through `app/api/*` or `python_scripts/*` using `SUPABASE_SERVICE_ROLE_KEY`, which bypasses RLS, and no client component talks to Supabase directly). **Owner ran it in the Supabase SQL Editor 2026-07-29 — applied successfully.** Remaining: dismiss the alert in the Supabase dashboard (Project ref `soxyqhgtzntrbinhcrdv`).
0a. ✅ **[Done] Emoji cleanup.** All UI emojis removed (`app/report/[id]/page.tsx`, `app/page.tsx`) — plain text/arrows only now. Telegram message emoji (🔔 in `notify_feedback.py`) is not web UI — left as-is. **Owner does not want emojis in the UI — don't add any going forward.**
0. ✅ **[Done, 2026-07-27] Shorten the app URL.** Owner bought `drakmal.com` (Porkbun) and connected it to Vercel as `relocate.drakmal.com` (Production); DNS via Porkbun (CNAME `relocate` → Vercel). `APP_BASE_URL` + Turnstile hostname allowlist updated to match in Vercel, GitHub secrets, and Cloudflare. Do NOT share the `-git-main-…vercel.app` URL — it's login-gated + not Turnstile-allowlisted.
1. ✅ **[Security, HIGH] Done, 2026-07-27.** Email is only *format*-checked by itself — a fake `@moh.gov.my` would otherwise pass and get its own 3/week quota → could run up Google cost. Three layers now all live:
   - ✅ **IP-based rate limit** (`app/api/requests/route.ts`, `IP_WEEKLY_LIMIT = 5`). `db/003_add_ip_rate_limit.sql` is applied — the `ip_address` column exists and the cap is active.
   - ✅ **Cloudflare Turnstile CAPTCHA** — coded, keys set, and functionally confirmed working (see env-var section above).
   - ✅ **True email verification (magic-link), committed and deployed.** `POST /api/requests` no longer writes `tracking_requests` directly — it creates a `pending_verifications` row (`db/008`, applied) and emails a link; only clicking it (`POST /api/verify/[token]`) creates the real row and spends quota. Kept **hard-fail** by design: if the verification email can't be sent, the submission is rejected outright, since an unconfirmed inbox shouldn't count. Expired, unconfirmed rows (30 min TTL) are swept daily by `cleanup_pending_verifications.py`.
2. ✅ **[Reliability] Done.** Anchor creation moved out of the submit path into a
   background Action. Submit now just saves the request; `create_anchors.yml`
   runs `run_pending_anchors.py` every 10 min to build anchors for any request
   that has none. Removes the ~14 s inline Overpass call (Vercel timeout risk).
3. ✅ **[Data] Third geocoding pass, 2026-07-27: 63 → 33 approximate rows.** Live-DB cleanup (direct Supabase PATCH, no new script committed): **7 fixed by copying coordinates from an already-correctly-geocoded sibling facility** (e.g. the mobile/co-located dental clinic "at KK Larkin" now uses KK Larkin's real coordinates instead of a generic state-centre fallback — this also caught 3-4 rows that shared one identical wrong Sabah fallback point across unrelated towns). **23 more fixed via targeted Nominatim place-name lookups** (town/district names like Tenom, Sarikei, Kudat, Betong, Jasin, Segambut, the Al-Muktafi Billah Shah mosque in Kuala Terengganu) — the automated `refine_coords*.py` regex-based name parsing had missed these (commas in names like "KK Palong 9,10,11", nested "di X di Y" wording, etc.), but a hand-curated query per facility found them. One low-confidence match was explicitly rejected (a generic "oil palm plantation" hit ~400km from the actual Mukah-division facility it was supposed to be). **33 remain unresolved** — almost all rural Sabah interior KK village names and Sarawak riverine "Nanga X" longhouse settlements with no OSM/Nominatim presence at all, consistent with the known KD/interior coverage gap (see item 4). A few peninsular ones (KK Bukit Waha Johor, KK Bandar Tasik Mutiara Penang, a Kedah school-based dental clinic, one Mukah-division facility) also failed multiple query variants and are left as-is rather than guess.
4. ✅ **[Data] KK reconciliation DONE for all 16 states** vs the official directory (`data/klinik_kesihatan_official.json`, already committed — **no scraping needed**; the MOH site's old scrape URL now 404s). Tool: **`add_state_facilities.py "<state key>" [--dry-run]`** — dedupes against the LIVE DB by a canonicalized name (handles Ayer/Air, Seri/Sri, Sg/Sungei, and KK/Klinik/KK1M/KD prefixes; excludes dental/lab rows), geocodes by postcode with a town fallback, parents to nearest PKD. Batch run added **14 KKs** across Kedah/Kelantan/Melaka/Sarawak/Selangor/Terengganu/WP KL/Pulau Pinang; the rest were already fully covered. Notes: `SKIP` set holds verified same-place duplicates the official list names without a locality suffix (e.g. Selangor "KK Batu 9" == "…Batu 9, Cheras"); one town-fallback miss (KK Paloh) was manually corrected to Belawai. **Klinik Desa (KD):** no official KD directory exists anywhere we can reach (MOH site gone; the committed JSON is KK-only), so KD can't be fully reconciled like KK. `add_kd_from_osm.py` does a best-effort OSM top-up (filters "Opposite …" markers + duplicate-mapped nodes, dedupes vs live DB, parents to nearest PKD) — it added ~13 named KD OSM had that we lacked. OSM's KD coverage is thinner than our DB, so the real gap (esp. Sabah/Sarawak, which have hundreds of KD in reality) remains **unfillable without an official KD source**. Perak's KD (243) is the only near-complete state, from an earlier manual scrape. **Sarawak — Bahagian Mukah** was filled from a user-supplied list (`data/mukah_sarawak_facilities.xlsx`, public Google-search info) via `add_mukah_sarawak_facilities.py`: added 1 hospital (Daro) + 9 clinics (KKIA Mukah/Dalat, KK1M Bandar/Sesok Mukah, KK Igan/Ladang Sawit/Nanga Semah/Betanak/Tekajong). Same pattern works for any other division/state list a user provides. **Sabah — filled statewide** from the Sabah SKN directory (`skn.sabah.gov.my/iknow/kesihatan-keselamatan/fasiliti-kesihatan` — server-rendered, NOT bot-blocked; curl works) via `add_sabah_facilities.py` reading `data/sabah_health_facilities.json` (353 scraped). Added **225** (6 hospitals + 164 KD + 55 KK/KKom), category by NAME prefix (source's type column mislabels KD as "Klinik Kesihatan"), parented by the district column. Sabah KD went 5 → 169. 20 rows that wouldn't geocode were snapped to their district PKD centroid (`source=pkd_district_centroid`). **This is the model for the remaining East-Malaysia gaps** — state health-dept directories, not moh.gov.my. **Melaka — 19 KD/KK1M** added from municipal-council pages (mpjasin.gov.my has exact GPS per KD; mphtj.gov.my by geocode) → `data/melaka_council_kd.json`; excluded private Poliklinik/Hospital Pantai.

**⚠️ Parenting gotcha:** scripts that parent by *nearest-PKD-by-coordinate* (`add_state_facilities`, `add_penang`, `add_kd_from_osm`, `add_mukah`) mis-assign facilities near district borders — e.g. Mukah-town clinics landed under PKD Dalat, so they didn't show under PKD Mukah. Prefer parenting by the source's **district/address** (as `add_sabah` does). Fixed cases: Mukah-town clinics → PKD Mukah; KK Tepus → PKD Selangau. If a user reports "can't see X under PKD Y", check the parent_location_id vs the address district first.
5. ✅ **[Feature] Done, 2026-07-27.** Email is fully live. `RESEND_API_KEY`/`RESEND_FROM`/`APP_BASE_URL` confirmed set in both Vercel and GitHub secrets, `drakmal.com` DNS records added at Porkbun and verified in Resend, and delivery confirmed via several real test sends (see env-var section for the full troubleshooting trail — corrupted `RESEND_FROM`, missing DNS records, propagation lag). The magic-link flow (item 1) and report-ready email (`generate_report.py`'s `send_report_email`) are both wired and live.
6. ⚠️ **[2 bugs FIXED — needs re-test] Empty reports, root-caused twice.**
   - **Bug 1 (fixed earlier):** every Google Distance Matrix call failed with `INVALID_REQUEST: departure_time is in the past` — `collect_traffic.py` captured `now` once at the start, but a 100+ anchor batch takes minutes, so by call time the leave-home moment was past. Fixed: `get_google_maps_travel_time` clamps `departure_time` to `now+1min` if it's past.
   - **Bug 2 (found after Bug 1's fix still produced 0 logs):** GitHub Actions' `schedule` trigger is unreliable — runs are commonly delayed 30-60+ min, so the old **40-minute** collection window (`leave_home -30min` to `+10min`) was frequently missed entirely by the actual (jittery) run cadence. Confirmed via run history: requests with 100+ anchors and valid leave-times still got 0 logs across multiple days because no run ever landed inside their narrow window. **Fixed:** removed the upper bound — `collect_traffic.py` now collects any time from `leave_home -30min` onward (daily dedup still caps it to one point/day; the workflow's own 05:00–12:00 MYT cron already bounds how late a run can happen, so no separate upper cutoff is needed).
   - Also: report page shows leave-home/arrive-office times; Groq prompt is now grounded with a computed per-area reliability summary (see #9).
   - ✅ **RE-TEST DONE 2026-08-07 MYT — both bugs confirmed fixed.** The three post-fix requests all collect traffic normally, while every pre-fix request still shows 0 logs (expected — they predate the fix, not a regression):

     | Request | Created | Anchors | Traffic logs | Report |
     | --- | --- | --- | --- | --- |
     | PKD Yan (`e41a9c53`) | 07-27 | 207 | 158 | generated 07-29 |
     | PKD Kangar (`f2365202`) | 07-27 | 103 | 223 | completed |
     | PKD Kuala Kangsar (`bac013f1`) | 07-27 | 113 | 431 | generated 08-07 (see item 11) |

     Pre-fix, for contrast: PKD Sik (153 anchors / **0 logs**), PKD Kangar 07-19 (103 / **0**), PKD Kuala Kangsar 07-18 (113 / **0**). Item 6 is closed — the widened collection window works.
11. ✅ **[Report generation] Fixed 2026-08-07 — Groq token ceiling silently killed large reports.** PKD Kuala Kangsar sat `pending` for 4 days after its window ended, failing on every hourly run with `413 Request too large … tokens per minute (TPM): Limit 12000, Requested 21224`. Cause: `format_logs_for_prompt` emitted **one line per log row**, so prompt size scaled with anchor count × tracked days — 158 and 223 logs fit, 431 didn't. Fix: the function now emits **one line per collected date** (travel avg + min-max range + sample count + weather), bounding the prompt by window length (1-5 days) instead of anchor count. Measured on the failing request: **21,224 → ~883 tokens**. No quality loss — the per-area detail the report actually cites already came from `summarize_anchor_reliability`, and `build_prompt` explicitly instructs the model to "use the summary above". Also added `WMO_CONDITIONS`: `weather_condition` stores Open-Meteo's raw WMO `weather_code`, so the day line now reads `overcast` / `partly cloudy` rather than a bare `3`. The stuck request was re-run manually and completed (report + Resend email HTTP 200).
9. ✅ **[Report quality + rental] Done, `db/006`/`db/007` applied 2026-07-27.**
   - **Farther-area + route-directness analysis** (`generate_report.py`): `distance_km` is now captured from the Distance Matrix response (`db/006_add_distance_km.sql`) so the report can name the **farthest area still within a 60-min commute**, not just the top-3 most reliable. A speed-based heuristic (distance/time) flags whether a route "looks direct/highway-like" vs "indirect/likely congested" — explicitly framed to the LLM as an inference, never as a fabricated road name or toll fact (we have no Routes/Directions API call for that — would be a separate paid API + owner decision if wanted later).
   - **Rental price + broken link:** iProperty's own `?q=`/`_freetext=` params are silently ignored (confirmed by hand — no public free-text search exists, only an internal `districtCode` via their autocomplete widget), so the direct link always fell back to the generic all-Malaysia listing page. Replaced with a `site:iproperty.com.my` Google-search deep link (verified to surface real listings + prices for a test area). Also: Groq now returns a delimited `###AREA_ESTIMATES###` block with a typical RM range per top/farthest area, parsed and stored in `area_price_estimates` (`db/007_add_rental_estimates.sql`) and shown on the report page labeled "AI estimate, not a live listing" (no live rental-price API exists for Malaysia, so this is disclosed, not fabricated as fact).
7. ✅ **[Collection] Done.** Collection window widened to **05:00–12:00 MYT** (`daily_tracker.yml` cron `*/15 21-23,0-3 * * *`); with the 30-min lead this covers leave-home times up to ~12:15. Widen further only if users report leaving home after noon.
8. ✅ **[Feedback] Done — Telegram alerts live.** "Can't find your facility?" form sits **beside Box 5** on `app/page.tsx` -> `app/api/feedback/route.ts` -> Supabase `feedback` table (`db/004`, run). Reports also push to **Telegram** via `notify_feedback.yml` (only when new — never messages on empty). `db/005_feedback_notified.sql` applied 2026-07-27 — alerts are live, not just inert-safe. Light spam guard: 20 reports/IP/day.
10. ✅ **[Copy/UX overhaul] Done, 2026-07-27/28**, owner-directed. Site branding: dropped "KKM" everywhere (browser title, all page eyebrows, email subject/footer/from-name) — now just "Relocation Planner". Headline "Where Should You Live?" -> "**Where Should You Stay?**" (the tool's output is a suggested area, not a verdict on living somewhere). Reworked several info-card blurbs for clarity (dropped a stale "weather data" mention, the 1-5-working-day aside, an over-explained "we collect one data point per working day" line). Removed the reference-ID card from the report-status page and the post-confirm message — no longer needed since report delivery is by email, not by the user keeping an ID. Main-page submit button renamed to **"Request real-time traffic tracking"**. Reworded the whole email/verify-flow language to stop implying the *email link itself* confirms the request (see Gotchas below) — email button is now **"Go to confirmation page"**, and the report-ready date/time moved from the post-submit message to the post-confirm "tracking started" screen (shown once, only once the request is actually confirmed).

## Gotchas
- **⚠️ `run_due_reports.py` fails silently — a green "Generate Due Reports" run does NOT mean reports were sent.** Its loop catches every per-request exception, prints `FAILED <id>: <err>`, and still exits 0, so one broken request never blocks the others *and never surfaces as a failed workflow*. This is how the Groq 413 (item 11) went unnoticed for 4 days. **To check for real:** query `tracking_requests` for rows with `status != 'completed'` whose window has ended, or grep a run log for `FAILED` — don't trust the green check. Making it exit non-zero on any failure is still an open option (rejected so far only because it means more failure email).
- **"The job was not acquired by Runner of type hosted" = GitHub infrastructure, not your code.** Scheduled workflows periodically fail with this after sitting ~15 min in the queue; no step ever runs, so `gh run view --log-failed` returns nothing at all (that empty output is the tell). Happened to `notify_feedback` + `create_anchors` on 2026-08-06. **Nothing to fix, and no data is lost** — both scripts pull from a work queue rather than reacting to an event (`notify_feedback` selects `notified_at IS NULL`; `run_pending_anchors` selects non-completed requests with zero anchors), so a skipped run is picked up by the next one. Same free-tier scheduling congestion behind bug 2.
- **The magic-link email is deliberately two-step — this is not a bug.** Clicking the
  link/button in the verification email only navigates to `app/verify/[token]/page.tsx`
  (a safe `GET`, no side effects); the request is only actually created when the user
  clicks that page's own button, which fires the `POST`. Reason: corporate email link
  scanners (Microsoft 365 Safe Links, some AV/gateway products) auto-fetch every URL in
  an incoming email *before* a human opens it. If the email link itself performed the
  confirm, a scanner's pre-fetch would silently burn the one-time token and create a
  phantom request before the real user ever clicked. Keep this two-step shape even
  though the email button and the page's button can look redundant — see item 10's copy
  changes for how the wording was adjusted to make the distinction clearer without
  removing the second step.
- **⚠️ NEVER open a live browser preview of `npm run dev` on this machine.** Owner's
  laptop has a weak GPU (NVIDIA MX150) that crashes hard (unrecoverable — requires
  full Claude Code reinstall) under local rendering load, and opening a rendered
  dev-server tab in a browser is the trigger. To verify the app works: run
  `npm run build` (compile check), or `curl -s -o /dev/null -w "%{http_code}"
  http://localhost:3000/<path>` against a backgrounded `npm run dev` process for
  a status-code check. Do not use any tool that renders/screenshots the page in
  an actual browser (Chrome preview, Simple Browser, etc.) unless the owner
  explicitly asks for a visual check.
- Google Geocoding is disabled on the key → use **Nominatim** (postcode query works best for MY).
- `moh.gov.my` and `mudah.my` block bots (HTTP 403) → scrape via the in-app **browser** using same-origin `fetch()`.
- `next build` while `next dev` is running corrupts `.next` (clear it + restart dev).
- Facility geocoding on public Nominatim is rate-limited — run one geocoding job at a time.
