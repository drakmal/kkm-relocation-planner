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
- `app/lib/` — `originAnchors` (anchor discovery), `holidays`, `rental`, `email`
- `python_scripts/` — seeders (`seed_*`, `add_perak_facilities`, `add_penang_facilities` (state-reconcile template), `reconcile_perlis`, `refine_coords*`), `collect_traffic`, `run_due_reports`, `generate_report`, `find_anchors`, `run_pending_anchors` (background anchor builder)
- `data/` — `public_holidays_my.json`, `klinik_kesihatan_official.json`, `klinik_kesihatan_missing.json`
- `.github/workflows/` — `daily_tracker.yml` (collection), `create_anchors.yml` (anchor builder), `generate_reports.yml`, `keep_alive.yml`

## Facility data (Supabase `locations`, ~3,119 rows)
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
4. **[Data]** **Perlis + Perak + Pulau Pinang** reconciled vs the official directory. **Key discovery:** the official KK directory for ALL 16 states is already committed at `data/klinik_kesihatan_official.json` (name + address + `in_osm` flag) — **no browser scraping needed for KK**. Use `add_penang_facilities.py` as the **template**: copy it, change `STATE`/`JKN_NAME`/`ORIGIN`, dry-run, then run. It dedupes against the LIVE DB by normalized name, geocodes by postcode, and parents to the nearest PKD. (Penang added just 1 missing KK — OSM coverage was already good; other states will vary.) **Caveat:** that JSON is KK-only — it does NOT include Klinik Desa (KD); Perak's KD came from a fuller manual scrape. Remaining unreconciled: Johor, Kedah, Kelantan, Melaka, N. Sembilan, Pahang, Sabah, Sarawak, Selangor, Terengganu, WP KL, WP Labuan, WP Putrajaya.
5. **[Feature, DEFERRED by owner]** Activate email: buy any domain (need NOT be moh.gov.my) → verify in Resend → set `RESEND_FROM` to it in Vercel + GitHub. Then confirmation + report emails auto-send. **Owner is not buying a domain for now** — revisit if another project needs one (then item #1's email-OTP option also unblocks). App runs fine on the reference-ID + `/report/[id]` link flow meanwhile.
6. **[Testing]** Full report pipeline not yet exercised with real collected traffic data (needs a real request run through a weekday collection cycle + `run_due_reports`).
7. **[Collection]** Window is 05:00–10:00 MYT; widen the `daily_tracker.yml` cron if users leave home after 10am.

## Gotchas
- Google Geocoding is disabled on the key → use **Nominatim** (postcode query works best for MY).
- `moh.gov.my` and `mudah.my` block bots (HTTP 403) → scrape via the in-app **browser** using same-origin `fetch()`.
- `next build` while `next dev` is running corrupts `.next` (clear it + restart dev).
- Facility geocoding on public Nominatim is rate-limited — run one geocoding job at a time.
