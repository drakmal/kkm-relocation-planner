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
- Email: **Resend** — keys/domain configured in Vercel + GitHub secrets as of 2026-07-27,
  but an actual live send hasn't been re-verified yet (see pending item 5). App still
  falls back to an on-screen **reference ID + `/report/[id]` link** (Option B) meanwhile;
  a stricter hard-fail magic-link flow is built but not yet committed (see item 1).

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
- `python_scripts/` — seeders (`seed_*`, `add_perak_facilities`, `add_penang_facilities`, `add_state_facilities` (generic KK reconciler — `"<state key>" [--dry-run]`), `add_kd_from_osm` (best-effort KD top-up from OSM), `add_mukah_sarawak_facilities`, `add_sabah_facilities` (Sabah SKN directory — hospitals+KK+KD), `reconcile_perlis`, `refine_coords*`), `collect_traffic`, `run_due_reports`, `generate_report`, `find_anchors`, `run_pending_anchors` (background anchor builder), `notify_feedback` (feedback -> Telegram)
- `data/` — `public_holidays_my.json`, `klinik_kesihatan_official.json`, `klinik_kesihatan_missing.json`, `sabah_health_facilities.json` (SKN scrape), `mukah_sarawak_facilities.xlsx`
- `.github/workflows/` — `daily_tracker.yml` (collection), `create_anchors.yml` (anchor builder), `generate_reports.yml`, `keep_alive.yml`

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
| `RESEND_API_KEY` / `RESEND_FROM` / `APP_BASE_URL` | ✅ set (domain-verified send still unconfirmed — see item 5) | ✅ set |
| `NEXT_PUBLIC_TURNSTILE_SITE_KEY` | ✅ set | — |
| `TURNSTILE_SECRET_KEY` | ✅ set | — |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | — | ✅ set (still needs `db/005_feedback_notified.sql` run — see item 8) |

GitHub secrets are all set (`gh secret list`). Vercel env confirmed to have the same set (owner-confirmed 2026-07-27).

**Turnstile CAPTCHA is coded and keys are set in Vercel** — should be live. Client
renders the widget only when `NEXT_PUBLIC_TURNSTILE_SITE_KEY` is present; server
verifies only when `TURNSTILE_SECRET_KEY` is present. Not yet functionally re-verified
end-to-end on the live form since keys were added — worth a quick check.

## Scheduled jobs (GitHub Actions — all verified running)
- `daily_tracker.yml` — `collect_traffic.py` every 15 min in 05:00–10:00 MYT; collects per request near its leave-home time; skips weekends/holidays/off-window; once/day dedup.
- `create_anchors.yml` — `run_pending_anchors.py` every 10 min; builds Overpass anchors for any non-completed request that has none (moved off the submit path).
- `generate_reports.yml` — `run_due_reports.py` hourly; generates + (dormant) emails reports whose window ended.
- `keep_alive.yml` — pings Supabase every 3 days (prevents free-tier pause).
- `notify_feedback.yml` — `notify_feedback.py` every 15 min; pushes NEW `feedback` rows to Telegram (only when there's un-notified feedback — silent otherwise; stamps `notified_at`). Inert until `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` secrets + `db/005` are set.

## STATUS: deployed & verified end-to-end (reads, submit+anchors, MOH gate, all 3 Actions succeed).

## Pending / next steps (priority order)
0a. ✅ **[Done] Emoji cleanup.** All UI emojis removed (`app/report/[id]/page.tsx`, `app/page.tsx`) — plain text/arrows only now. Telegram message emoji (🔔 in `notify_feedback.py`) is not web UI — left as-is. **Owner does not want emojis in the UI — don't add any going forward.**
0. ✅ **[Done, 2026-07-27] Shorten the app URL.** Owner bought `drakmal.com` (Porkbun) and connected it to Vercel as `relocate.drakmal.com` (Production); DNS via Porkbun (CNAME `relocate` → Vercel). `APP_BASE_URL` + Turnstile hostname allowlist updated to match in Vercel, GitHub secrets, and Cloudflare. Do NOT share the `-git-main-…vercel.app` URL — it's login-gated + not Turnstile-allowlisted.
1. **[Security, HIGH]** Email is only *format*-checked — a fake `@moh.gov.my` passes and each fake email gets its own 3/week quota → can run up Google cost.
   - ✅ **IP-based rate limit implemented** (`app/api/requests/route.ts`, `IP_WEEKLY_LIMIT = 5`). Best-effort: `ip_address` is written via a post-insert update and the gate is skipped if the column is missing, so it deploys safely before migration. **ACTION REQUIRED:** run `db/003_add_ip_rate_limit.sql` in the Supabase SQL editor to add the `ip_address` column + index; the cap is inert until then.
   - ✅ **IP cap = 5/IP/week** (`IP_WEEKLY_LIMIT = 5`) — a bit above the 3/email cap so a shared office (several colleagues behind one NAT) isn't blocked in normal use.
   - ✅ **Cloudflare Turnstile CAPTCHA implemented and keys set in Vercel** (client widget in `app/page.tsx`, server verify in `route.ts`). Not yet functionally re-verified live since the keys were added — quick check pending.
   - ⏳ **True email OTP / magic-link verification — built, not yet committed.** A full pending-verification flow sits uncommitted in the working tree (`db/008_add_pending_verifications.sql`, `app/api/verify/[token]/route.ts`, `app/verify/[token]/page.tsx`, `app/lib/email.ts`'s `sendVerificationEmail`, and `app/api/requests/route.ts` now creates a `pending_verifications` row + emails a confirm link instead of writing `tracking_requests` directly). Decision made: keep it **hard-fail** — if the verification email can't be sent, the submission is rejected (no reference-ID fallback), since a submission without a proven inbox shouldn't count. Blocked on running `db/008` first, then committing.
2. ✅ **[Reliability] Done.** Anchor creation moved out of the submit path into a
   background Action. Submit now just saves the request; `create_anchors.yml`
   runs `run_pending_anchors.py` every 10 min to build anchors for any request
   that has none. Removes the ~14 s inline Overpass call (Vercel timeout risk).
3. **[Data]** ~61 facilities still on approximate (state-centre) coords (no usable postcode/OSM entry).
4. ✅ **[Data] KK reconciliation DONE for all 16 states** vs the official directory (`data/klinik_kesihatan_official.json`, already committed — **no scraping needed**; the MOH site's old scrape URL now 404s). Tool: **`add_state_facilities.py "<state key>" [--dry-run]`** — dedupes against the LIVE DB by a canonicalized name (handles Ayer/Air, Seri/Sri, Sg/Sungei, and KK/Klinik/KK1M/KD prefixes; excludes dental/lab rows), geocodes by postcode with a town fallback, parents to nearest PKD. Batch run added **14 KKs** across Kedah/Kelantan/Melaka/Sarawak/Selangor/Terengganu/WP KL/Pulau Pinang; the rest were already fully covered. Notes: `SKIP` set holds verified same-place duplicates the official list names without a locality suffix (e.g. Selangor "KK Batu 9" == "…Batu 9, Cheras"); one town-fallback miss (KK Paloh) was manually corrected to Belawai. **Klinik Desa (KD):** no official KD directory exists anywhere we can reach (MOH site gone; the committed JSON is KK-only), so KD can't be fully reconciled like KK. `add_kd_from_osm.py` does a best-effort OSM top-up (filters "Opposite …" markers + duplicate-mapped nodes, dedupes vs live DB, parents to nearest PKD) — it added ~13 named KD OSM had that we lacked. OSM's KD coverage is thinner than our DB, so the real gap (esp. Sabah/Sarawak, which have hundreds of KD in reality) remains **unfillable without an official KD source**. Perak's KD (243) is the only near-complete state, from an earlier manual scrape. **Sarawak — Bahagian Mukah** was filled from a user-supplied list (`data/mukah_sarawak_facilities.xlsx`, public Google-search info) via `add_mukah_sarawak_facilities.py`: added 1 hospital (Daro) + 9 clinics (KKIA Mukah/Dalat, KK1M Bandar/Sesok Mukah, KK Igan/Ladang Sawit/Nanga Semah/Betanak/Tekajong). Same pattern works for any other division/state list a user provides. **Sabah — filled statewide** from the Sabah SKN directory (`skn.sabah.gov.my/iknow/kesihatan-keselamatan/fasiliti-kesihatan` — server-rendered, NOT bot-blocked; curl works) via `add_sabah_facilities.py` reading `data/sabah_health_facilities.json` (353 scraped). Added **225** (6 hospitals + 164 KD + 55 KK/KKom), category by NAME prefix (source's type column mislabels KD as "Klinik Kesihatan"), parented by the district column. Sabah KD went 5 → 169. 20 rows that wouldn't geocode were snapped to their district PKD centroid (`source=pkd_district_centroid`). **This is the model for the remaining East-Malaysia gaps** — state health-dept directories, not moh.gov.my. **Melaka — 19 KD/KK1M** added from municipal-council pages (mpjasin.gov.my has exact GPS per KD; mphtj.gov.my by geocode) → `data/melaka_council_kd.json`; excluded private Poliklinik/Hospital Pantai.

**⚠️ Parenting gotcha:** scripts that parent by *nearest-PKD-by-coordinate* (`add_state_facilities`, `add_penang`, `add_kd_from_osm`, `add_mukah`) mis-assign facilities near district borders — e.g. Mukah-town clinics landed under PKD Dalat, so they didn't show under PKD Mukah. Prefer parenting by the source's **district/address** (as `add_sabah` does). Fixed cases: Mukah-town clinics → PKD Mukah; KK Tepus → PKD Selangau. If a user reports "can't see X under PKD Y", check the parent_location_id vs the address district first.
5. ⏳ **[Feature, IN PROGRESS as of 2026-07-27]** Activate email. `RESEND_API_KEY`/`RESEND_FROM`/`APP_BASE_URL` are confirmed set in **both** Vercel and GitHub secrets (owner-confirmed 2026-07-27, using the `drakmal.com` domain). Both confirmation-on-submit (`app/lib/email.ts` → `route.ts`) and report-ready (`generate_report.py`'s `send_report_email`) are fully coded and wired. **Still open:** an actual end-to-end send hasn't been re-verified since the domain move — need to confirm a real magic-link/confirmation email lands in an inbox, not just that the key is present. Once confirmed live, item #1's magic-link flow can be committed and deployed.
6. ⚠️ **[2 bugs FIXED — needs re-test] Empty reports, root-caused twice.**
   - **Bug 1 (fixed earlier):** every Google Distance Matrix call failed with `INVALID_REQUEST: departure_time is in the past` — `collect_traffic.py` captured `now` once at the start, but a 100+ anchor batch takes minutes, so by call time the leave-home moment was past. Fixed: `get_google_maps_travel_time` clamps `departure_time` to `now+1min` if it's past.
   - **Bug 2 (found after Bug 1's fix still produced 0 logs):** GitHub Actions' `schedule` trigger is unreliable — runs are commonly delayed 30-60+ min, so the old **40-minute** collection window (`leave_home -30min` to `+10min`) was frequently missed entirely by the actual (jittery) run cadence. Confirmed via run history: requests with 100+ anchors and valid leave-times still got 0 logs across multiple days because no run ever landed inside their narrow window. **Fixed:** removed the upper bound — `collect_traffic.py` now collects any time from `leave_home -30min` onward (daily dedup still caps it to one point/day; the workflow's own 05:00–12:00 MYT cron already bounds how late a run can happen, so no separate upper cutoff is needed).
   - Also: report page shows leave-home/arrive-office times; Groq prompt is now grounded with a computed per-area reliability summary (see #9).
   - **TO RE-TEST:** watch a fresh request (e.g. PKD Sik / PKD Kangar, submitted 2026-07-20/21) run through a full weekday collection cycle and confirm logs actually land + a real report generates.
9. ✅ **[Report quality + rental] Done.**
   - **Farther-area + route-directness analysis** (`generate_report.py`): `distance_km` is now captured from the Distance Matrix response (`db/006_add_distance_km.sql` — **ACTION REQUIRED**, was previously discarded) so the report can name the **farthest area still within a 60-min commute**, not just the top-3 most reliable. A speed-based heuristic (distance/time) flags whether a route "looks direct/highway-like" vs "indirect/likely congested" — explicitly framed to the LLM as an inference, never as a fabricated road name or toll fact (we have no Routes/Directions API call for that — would be a separate paid API + owner decision if wanted later).
   - **Rental price + broken link:** iProperty's own `?q=`/`_freetext=` params are silently ignored (confirmed by hand — no public free-text search exists, only an internal `districtCode` via their autocomplete widget), so the direct link always fell back to the generic all-Malaysia listing page. Replaced with a `site:iproperty.com.my` Google-search deep link (verified to surface real listings + prices for a test area). Also: Groq now returns a delimited `###AREA_ESTIMATES###` block with a typical RM range per top/farthest area, parsed and stored in `area_price_estimates` (`db/007_add_rental_estimates.sql` — **ACTION REQUIRED**) and shown on the report page labeled "AI estimate, not a live listing" (no live rental-price API exists for Malaysia, so this is disclosed, not fabricated as fact).
7. ✅ **[Collection] Done.** Collection window widened to **05:00–12:00 MYT** (`daily_tracker.yml` cron `*/15 21-23,0-3 * * *`); with the 30-min lead this covers leave-home times up to ~12:15. Widen further only if users report leaving home after noon.
8. ✅ **[Feedback] Built + Telegram alerts.** "Can't find your facility?" form sits **beside Box 5** on `app/page.tsx` -> `app/api/feedback/route.ts` -> Supabase `feedback` table (`db/004`, run). Reports also push to **Telegram** via `notify_feedback.yml` (only when new — never messages on empty). `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` are already set as GitHub secrets. **ACTION REQUIRED:** run `db/005_feedback_notified.sql` (still not applied) — alerts are inert until then. Until it's run, reports still save; read them in the Supabase Table Editor. Light spam guard: 20 reports/IP/day.

## Gotchas
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
