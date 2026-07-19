'use client';

import { FormEvent, useEffect, useRef, useState } from 'react';
import {
  workingDaysBetween,
  holidaysInRange,
  isWorkingDay,
  nextWorkingDay,
  toDateKey,
} from '@/app/lib/holidays';

type LocationOption = {
  id: string;
  name: string;
  tier: string;
  parent_id: string | null;
  latitude: number | null;
  longitude: number | null;
  category: string;
};

type FormState = {
  targetOfficeTier: string;
  targetOfficeName: string;
  targetLat: number | null;
  targetLng: number | null;
  travelMode: string;
  userEmail: string;
  exitHomeTime: string;
  arriveOfficeTime: string;
  startDate: string;
  endDate: string;
};

const MAX_WORKING_DAYS = 5;

// Advance from `start` until `n` working days (Mon-Fri, excl. holidays) are covered.
function addWorkingDays(start: Date, n: number): Date {
  const d = new Date(start);
  let count = isWorkingDay(d) ? 1 : 0;
  while (count < n) {
    d.setDate(d.getDate() + 1);
    if (isWorkingDay(d)) count += 1;
  }
  return d;
}

function defaultDates(): { startDate: string; endDate: string } {
  const start = nextWorkingDay();
  return { startDate: toDateKey(start), endDate: toDateKey(addWorkingDays(start, 3)) };
}

const initialForm: FormState = {
  targetOfficeTier: 'state',
  targetOfficeName: '',
  targetLat: null,
  targetLng: null,
  travelMode: 'car',
  userEmail: '',
  exitHomeTime: '06:30',
  arriveOfficeTime: '07:55',
  ...defaultDates(),
};

// Box 3 (KK / KKP) shows every clinic-tier facility EXCEPT Klinik Desa,
// which lives in its own Box 4.
const BOX3_CATEGORIES = new Set(['klinik_kesihatan', 'klinik_pergigian', 'klinik_1malaysia', 'government_clinic']);
// Box 2 ordering: hospitals first, then PKD, then PKPD.
const BOX2_ORDER: Record<string, number> = { hospital: 0, pkd: 1, pkpd: 2 };

// The report is ready shortly after the last tracking day's morning data is in
// (1 hour after the arrive-office time on the end date).
function computeReportReady(endDateKey: string, arriveOfficeTime: string): Date {
  const [y, m, d] = (endDateKey || '').split('-').map(Number);
  const date = y && m && d ? new Date(y, m - 1, d) : new Date();
  const [hh, mm] = (arriveOfficeTime || '07:55').split(':').map(Number);
  date.setHours((hh || 8) + 1, mm || 0, 0, 0);
  return date;
}

function formatReadyDateTime(date: Date): string {
  return date.toLocaleString('en-MY', { dateStyle: 'full', timeStyle: 'short' });
}

const WEEKLY_LIMIT = 3;

function isMohEmailClient(email: string): boolean {
  return /^[^@\s]+@([a-zA-Z0-9-]+\.)*moh\.gov\.my$/i.test((email || '').trim());
}

function formatShortDate(value?: string | null): string {
  if (!value) return 'the next weekly reset';
  return new Date(value).toLocaleString('en-MY', { dateStyle: 'medium', timeStyle: 'short' });
}

export default function HomePage() {
  const [form, setForm] = useState<FormState>(initialForm);
  const [status, setStatus] = useState<string | null>(null);
  const [savedRequestId, setSavedRequestId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingLocations, setLoadingLocations] = useState(false);
  const [boxOneOptions, setBoxOneOptions] = useState<LocationOption[]>([]);
  const [boxTwoOptions, setBoxTwoOptions] = useState<LocationOption[]>([]);
  const [boxThreeOptions, setBoxThreeOptions] = useState<LocationOption[]>([]);
  const [boxFourOptions, setBoxFourOptions] = useState<LocationOption[]>([]);
  const [boxFiveOptions, setBoxFiveOptions] = useState<LocationOption[]>([]);
  const [selectedBoxOneId, setSelectedBoxOneId] = useState('');
  const [selectedBoxTwoId, setSelectedBoxTwoId] = useState('');
  const [selectedBoxThreeId, setSelectedBoxThreeId] = useState('');
  const [selectedBoxFourId, setSelectedBoxFourId] = useState('');
  const [selectedBoxFiveId, setSelectedBoxFiveId] = useState('');
  const [usage, setUsage] = useState<{ used: number; limit: number; resetAt: string | null } | null>(null);
  const [limitModal, setLimitModal] = useState<{ resetAt: string | null } | null>(null);

  // "Can't find your facility?" feedback -> Supabase (see app/api/feedback).
  const [fbOpen, setFbOpen] = useState(false);
  const [fbFacility, setFbFacility] = useState('');
  const [fbMessage, setFbMessage] = useState('');
  const [fbEmail, setFbEmail] = useState('');
  const [fbStatus, setFbStatus] = useState<'idle' | 'sending' | 'sent'>('idle');
  const [fbError, setFbError] = useState<string | null>(null);

  async function submitFeedback(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFbError(null);
    if (!fbMessage.trim()) {
      setFbError('Please describe the issue or the facility you could not find.');
      return;
    }
    setFbStatus('sending');
    try {
      const res = await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: fbFacility.trim() ? 'missing_facility' : 'issue',
          facilityQuery: fbFacility.trim() || null,
          message: fbMessage.trim(),
          userEmail: fbEmail.trim() || null,
          pageContext: { selectedTarget: form.targetOfficeName || null },
        }),
      });
      const p = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(p.error || 'Could not send your report. Please try again.');
      setFbStatus('sent');
      setFbFacility('');
      setFbMessage('');
      setFbEmail('');
    } catch (err) {
      setFbStatus('idle');
      setFbError(err instanceof Error ? err.message : 'Could not send your report.');
    }
  }

  // Cloudflare Turnstile CAPTCHA. Only rendered when a site key is configured;
  // the server verifies the token (see app/api/requests/route.ts).
  const turnstileSiteKey = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY || '';
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  const turnstileRef = useRef<HTMLDivElement | null>(null);
  const turnstileWidgetId = useRef<string | null>(null);

  useEffect(() => {
    if (!turnstileSiteKey) return;
    const SCRIPT_ID = 'cf-turnstile-script';

    function renderWidget() {
      const turnstile = (window as unknown as { turnstile?: any }).turnstile;
      if (!turnstile || !turnstileRef.current || turnstileWidgetId.current !== null) return;
      turnstileWidgetId.current = turnstile.render(turnstileRef.current, {
        sitekey: turnstileSiteKey,
        callback: (token: string) => setTurnstileToken(token),
        'expired-callback': () => setTurnstileToken(null),
        'error-callback': () => setTurnstileToken(null),
      });
    }

    if (document.getElementById(SCRIPT_ID)) {
      renderWidget();
      return;
    }
    const script = document.createElement('script');
    script.id = SCRIPT_ID;
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    script.async = true;
    script.defer = true;
    script.onload = renderWidget;
    document.head.appendChild(script);
  }, [turnstileSiteKey]);

  // Clear the solved token and re-arm the widget so a new challenge is required
  // for the next submission.
  function resetTurnstile() {
    setTurnstileToken(null);
    const turnstile = (window as unknown as { turnstile?: any }).turnstile;
    if (turnstile && turnstileWidgetId.current !== null) {
      turnstile.reset(turnstileWidgetId.current);
    }
  }

  async function fetchLocations(parentId?: string | null, mode?: string) {
    const params = new URLSearchParams();
    if (parentId) params.set('parent_id', parentId);
    if (mode) params.set('mode', mode);

    const response = await fetch(`/api/locations?${params.toString()}`);
    if (!response.ok) {
      throw new Error('Unable to load location options');
    }
    return (await response.json()) as LocationOption[];
  }

  function sortByName(items: LocationOption[]) {
    return [...items].sort((a, b) => a.name.localeCompare(b.name, 'ms-MY'));
  }

  useEffect(() => {
    async function loadInitialOptions() {
      try {
        setLoadingLocations(true);
        const allOptions = await fetchLocations();
        const jknOptions = sortByName(
          allOptions.filter((item) => !item.parent_id && item.category === 'jkn'),
        );
        setBoxOneOptions(jknOptions);

        const otherOptions = sortByName(await fetchLocations(undefined, 'other'));
        setBoxFiveOptions(otherOptions);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unable to load locations');
      } finally {
        setLoadingLocations(false);
      }
    }

    void loadInitialOptions();
  }, []);

  // Box 1 (JKN) -> Box 2 (Hospital / PKD / PKPD)
  useEffect(() => {
    if (!selectedBoxOneId) {
      setBoxTwoOptions([]);
      setSelectedBoxTwoId('');
      setSelectedBoxThreeId('');
      setSelectedBoxFourId('');
      return;
    }

    async function loadBoxTwo() {
      try {
        const options = await fetchLocations(selectedBoxOneId);
        const districts = options.filter((item) => item.tier === 'district');
        districts.sort((a, b) => {
          const oa = BOX2_ORDER[a.category] ?? 9;
          const ob = BOX2_ORDER[b.category] ?? 9;
          if (oa !== ob) return oa - ob;
          return a.name.localeCompare(b.name, 'ms-MY');
        });
        setBoxTwoOptions(districts);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unable to load office levels');
      }
    }

    void loadBoxTwo();
  }, [selectedBoxOneId]);

  // Box 2 -> Box 3 (KK / KKP) and Box 4 (Klinik Desa)
  useEffect(() => {
    if (!selectedBoxTwoId) {
      setBoxThreeOptions([]);
      setBoxFourOptions([]);
      setSelectedBoxThreeId('');
      setSelectedBoxFourId('');
      return;
    }

    async function loadChildClinics() {
      try {
        const options = await fetchLocations(selectedBoxTwoId);
        const clinics = options.filter((item) => item.tier === 'clinic');
        setBoxThreeOptions(sortByName(clinics.filter((item) => BOX3_CATEGORIES.has(item.category))));
        setBoxFourOptions(sortByName(clinics.filter((item) => item.category === 'klinik_desa')));
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unable to load clinic options');
      }
    }

    void loadChildClinics();
  }, [selectedBoxTwoId]);

  // Show the user's remaining weekly quota once they've typed a valid MOH email.
  useEffect(() => {
    if (!isMohEmailClient(form.userEmail)) {
      setUsage(null);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        const r = await fetch(`/api/requests?email=${encodeURIComponent(form.userEmail.trim())}`);
        if (r.ok) setUsage(await r.json());
      } catch {
        /* ignore usage-lookup failures */
      }
    }, 500);
    return () => clearTimeout(timer);
  }, [form.userEmail]);

  function selectLocation(option: LocationOption | null, level: 'one' | 'two' | 'three' | 'four' | 'five') {
    if (!option) {
      setForm((current) => ({ ...current, targetOfficeName: '', targetOfficeTier: 'state', targetLat: null, targetLng: null }));
      return;
    }

    setForm((current) => ({
      ...current,
      targetOfficeName: option.name,
      targetOfficeTier: option.tier,
      targetLat: option.latitude,
      targetLng: option.longitude,
    }));

    if (level === 'one') {
      setSelectedBoxOneId(option.id);
      setSelectedBoxTwoId('');
      setSelectedBoxThreeId('');
      setSelectedBoxFourId('');
      setSelectedBoxFiveId('');
    } else if (level === 'two') {
      setSelectedBoxTwoId(option.id);
      setSelectedBoxThreeId('');
      setSelectedBoxFourId('');
      setSelectedBoxFiveId('');
    } else if (level === 'three') {
      // Box 3 and Box 4 are sibling categories under the same office: only one
      // can be the target at a time.
      setSelectedBoxThreeId(option.id);
      setSelectedBoxFourId('');
      setSelectedBoxFiveId('');
    } else if (level === 'four') {
      setSelectedBoxFourId(option.id);
      setSelectedBoxThreeId('');
      setSelectedBoxFiveId('');
    } else {
      // Box 5 (Others) is an independent path.
      setSelectedBoxFiveId(option.id);
      setSelectedBoxOneId('');
      setSelectedBoxTwoId('');
      setSelectedBoxThreeId('');
      setSelectedBoxFourId('');
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus(null);
    setSavedRequestId(null);
    setError(null);

    const workingDays = workingDaysBetween(form.startDate, form.endDate);
    if (workingDays < 1) {
      setError('Please choose a valid tracking period (the end date must be on or after the start date and include at least one working day).');
      return;
    }
    if (workingDays > MAX_WORKING_DAYS) {
      setError(`The tracking period covers ${workingDays} working days. Please choose a range of ${MAX_WORKING_DAYS} working days or fewer (weekends and public holidays are not counted).`);
      return;
    }

    if (turnstileSiteKey && !turnstileToken) {
      setError('Please complete the "I\'m human" check before submitting.');
      return;
    }

    const readyAt = formatReadyDateTime(computeReportReady(form.endDate, form.arriveOfficeTime));

    try {
      const response = await fetch('/api/requests', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...form,
          trackingDuration: `${form.startDate} to ${form.endDate}`,
          startDate: form.startDate,
          endDate: form.endDate,
          reportReadyText: readyAt,
          // Only the morning commute (home -> office) is tracked now.
          // arrival_time = arrive-office; return_time repurposed to carry the
          // leave-home time (used to schedule the pre-departure collection).
          arrivalTime: form.arriveOfficeTime,
          returnTime: form.exitHomeTime,
          exitHomeTime: form.exitHomeTime,
          arriveOfficeTime: form.arriveOfficeTime,
          turnstileToken,
          created_at: new Date().toISOString(),
        }),
      });

      const payload = await response.json();
      // Turnstile tokens are single-use; re-arm the widget for the next attempt
      // regardless of the outcome.
      resetTurnstile();
      if (response.status === 429) {
        setLimitModal({ resetAt: payload.resetAt ?? null });
        return;
      }
      if (!response.ok) {
        throw new Error(payload.error || 'Unable to save request');
      }

      setStatus(
        payload.emailed
          ? `Request received — your reference ID (${payload.id}) has been emailed to ${form.userEmail}. Your report will be ready by ${readyAt}.`
          : `Request received! Save your reference ID: ${payload.id}. Your report will be ready by ${readyAt} — open it any time with the button below (bookmark it).`,
      );
      setSavedRequestId(payload.id);
      setUsage((current) => (current ? { ...current, used: current.used + 1 } : current));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unexpected error');
    }
  }

  return (
    <main className="min-h-screen w-full p-4">
      <section className="card hero-card p-4 gap-4 flex flex-col w-full max-w-6xl mx-auto">
        <div className="hero-banner py-2 px-3 flex flex-col gap-1">
          <p className="eyebrow" style={{ margin: 0 }}>KKM Relocation Planner</p>
          <h1 className="text-lg font-semibold leading-tight m-0">Where Should You Live?</h1>
          <p className="helper text-white text-sm m-0">
            For health workers relocating or transferring — measure your real daily commute and find the most reliable areas to stay near your posting.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 w-full mt-2">
          <div className="col-span-1 flex flex-col gap-3">
            <article className="info-card">
              <h2>How it works</h2>
              <p>The tool automatically collects real-time traffic and weather data on working days, continuing until the end of the tracking period you set.</p>
            </article>
            <article className="info-card">
              <h2>What to do</h2>
              <p>Select the target office hierarchy, add your commute times, and submit the request for analysis.</p>
            </article>
            <article className="info-card">
              <h2>What will happen next</h2>
              <p>Your detailed relocation report is generated automatically once the system finishes collecting traffic data for your selected period (1–5 working days). View it any time using the reference ID and link you receive after submitting.</p>
            </article>
            <article className="info-card">
              <h2>Access &amp; fair use</h2>
              <ul style={{ margin: 0, padding: 0, listStyle: 'none', color: '#4b5f74', lineHeight: 1.5, textAlign: 'left' }}>
                <li>• Restricted to MOH staff (<strong>moh.gov.my</strong> email).</li>
                <li>• Maximum <strong>3 requests per week</strong>.</li>
                <li>• A weekly usage counter is shown; when the free quota is reached the planner pauses.</li>
                <li>• The service goes offline until the next weekly reset when the free limit is hit.</li>
              </ul>
            </article>
            <article className="info-card">
              <h2>Can&apos;t find your facility?</h2>
              {fbStatus === 'sent' ? (
                <p className="success" style={{ margin: 0 }}>
                  Thanks — your report was sent. We&apos;ll use it to improve the facility list.
                </p>
              ) : (
                <>
                  <p style={{ marginTop: 0 }}>
                    Our facility list may be incomplete or slightly off. Tell us what&apos;s missing or wrong and we&apos;ll fix it.
                  </p>
                  {fbOpen ? (
                    <form onSubmit={submitFeedback} className="flex flex-col gap-2">
                      <label style={{ fontSize: '0.85rem' }}>
                        Facility name (if missing)
                        <input
                          type="text"
                          placeholder="e.g. Klinik Kesihatan Kampung ..."
                          value={fbFacility}
                          onChange={(e) => setFbFacility(e.target.value)}
                        />
                      </label>
                      <label style={{ fontSize: '0.85rem' }}>
                        Details / issue
                        <textarea
                          required
                          rows={3}
                          placeholder="What's missing or wrong? Where is it located?"
                          value={fbMessage}
                          onChange={(e) => setFbMessage(e.target.value)}
                        />
                      </label>
                      <label style={{ fontSize: '0.85rem' }}>
                        Your email (optional, only if you want a reply)
                        <input
                          type="email"
                          placeholder="name@moh.gov.my"
                          value={fbEmail}
                          onChange={(e) => setFbEmail(e.target.value)}
                        />
                      </label>
                      {fbError ? <p className="error" style={{ margin: 0 }}>{fbError}</p> : null}
                      <button type="submit" disabled={fbStatus === 'sending'}>
                        {fbStatus === 'sending' ? 'Sending…' : 'Send report'}
                      </button>
                    </form>
                  ) : (
                    <button type="button" onClick={() => setFbOpen(true)}>Report a missing or wrong facility</button>
                  )}
                </>
              )}
            </article>
          </div>

          <form onSubmit={handleSubmit} className="col-span-1 lg:col-span-2 flex flex-col gap-3 pr-2">
            <div className="section-heading">
              <h2>Target office</h2>
              <p>Select the most relevant office level for your relocation planning.</p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label>
                Box 1: JKN
                <select
                  value={selectedBoxOneId}
                  onChange={(e) => {
                    const option = boxOneOptions.find((item) => item.id === e.target.value) || null;
                    setSelectedBoxOneId(e.target.value);
                    selectLocation(option, 'one');
                  }}
                >
                  <option value="">Select JKN</option>
                  {boxOneOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.name}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Box 2: Hospital / PKD / PKPD
                <select
                  value={selectedBoxTwoId}
                  onChange={(e) => {
                    const option = boxTwoOptions.find((item) => item.id === e.target.value) || null;
                    setSelectedBoxTwoId(e.target.value);
                    selectLocation(option, 'two');
                  }}
                  disabled={!selectedBoxOneId || loadingLocations}
                >
                  <option value="">{selectedBoxOneId ? 'Select hospital, PKD or PKPD' : 'Choose box 1 first'}</option>
                  {boxTwoOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label>
                Box 3: KK / KKP
                <select
                  value={selectedBoxThreeId}
                  onChange={(e) => {
                    const option = boxThreeOptions.find((item) => item.id === e.target.value) || null;
                    setSelectedBoxThreeId(e.target.value);
                    selectLocation(option, 'three');
                  }}
                  disabled={!selectedBoxTwoId || loadingLocations}
                >
                  <option value="">{selectedBoxTwoId ? 'Select KK or KKP' : 'Choose box 2 first'}</option>
                  {boxThreeOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.name}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Box 4: Klinik Desa
                <select
                  value={selectedBoxFourId}
                  onChange={(e) => {
                    const option = boxFourOptions.find((item) => item.id === e.target.value) || null;
                    setSelectedBoxFourId(e.target.value);
                    selectLocation(option, 'four');
                  }}
                  disabled={!selectedBoxTwoId || loadingLocations}
                >
                  <option value="">{selectedBoxTwoId ? 'Select Klinik Desa' : 'Choose box 2 first'}</option>
                  {boxFourOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label>
                Box 5: Others
                <select
                  value={selectedBoxFiveId}
                  onChange={(e) => {
                    const option = boxFiveOptions.find((item) => item.id === e.target.value) || null;
                    setSelectedBoxFiveId(e.target.value);
                    selectLocation(option, 'five');
                  }}
                  disabled={loadingLocations}
                >
                  <option value="">Select other office</option>
                  {boxFiveOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="selected-office">
              <span className="selected-badge">Selected target</span>
              <strong>{form.targetOfficeName || 'No selection yet'}</strong>
              <span className="muted">Tier: {form.targetOfficeTier || 'Pending'}</span>
            </div>

            {form.targetLat != null && form.targetLng != null ? (
              <div className="target-map mb-2">
                <iframe
                  title="Selected target location"
                  width="100%"
                  height="220"
                  loading="lazy"
                  style={{ border: 0, borderRadius: '12px', display: 'block' }}
                  src={`https://www.openstreetmap.org/export/embed.html?bbox=${form.targetLng - 0.02},${form.targetLat - 0.015},${form.targetLng + 0.02},${form.targetLat + 0.015}&layer=mapnik&marker=${form.targetLat},${form.targetLng}`}
                />
                <p className="muted" style={{ fontSize: '0.78rem', margin: '0.3rem 0 0' }}>
                  📍 {form.targetOfficeName} — {form.targetLat.toFixed(5)}, {form.targetLng.toFixed(5)}
                </p>
              </div>
            ) : (
              <div className="mb-2" />
            )}

            <div className="section-heading">
              <h2>Commute time</h2>
              <p>Capture your morning journey from home to the office.</p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label>
                Leave Home
                <input
                  type="time"
                  value={form.exitHomeTime}
                  onChange={(e) => setForm((current) => ({ ...current, exitHomeTime: e.target.value }))}
                />
              </label>
              <label>
                Arrive at Office
                <input
                  type="time"
                  value={form.arriveOfficeTime}
                  onChange={(e) => setForm((current) => ({ ...current, arriveOfficeTime: e.target.value }))}
                />
              </label>
            </div>

            <div className="section-heading">
              <h2>Tracking period</h2>
              <p>Pick the start and end dates (max {MAX_WORKING_DAYS} working days). Weekends and public holidays are skipped automatically.</p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label>
                Start date
                <input
                  type="date"
                  value={form.startDate}
                  min={toDateKey(new Date())}
                  onChange={(e) => setForm((current) => ({ ...current, startDate: e.target.value }))}
                />
              </label>
              <label>
                End date
                <input
                  type="date"
                  value={form.endDate}
                  min={form.startDate || toDateKey(new Date())}
                  onChange={(e) => setForm((current) => ({ ...current, endDate: e.target.value }))}
                />
              </label>
            </div>

            {(() => {
              const wd = workingDaysBetween(form.startDate, form.endDate);
              const hols = holidaysInRange(form.startDate, form.endDate);
              const over = wd > MAX_WORKING_DAYS;
              return (
                <div className="info-card">
                  <p style={{ margin: 0 }}>
                    {wd < 1 ? (
                      'Select a valid date range (the end date must be on or after the start date).'
                    ) : (
                      <>
                        This period covers <strong>{wd} working day{wd === 1 ? '' : 's'}</strong>
                        {over ? <span className="error"> — over the {MAX_WORKING_DAYS}-day limit</span> : ''}. Weekends{' '}
                        {hols.length ? 'and the public holidays below ' : ''}are not tracked.
                      </>
                    )}
                  </p>
                  {hols.length ? (
                    <ul style={{ margin: '0.4rem 0 0', paddingLeft: '1.1rem', color: '#4b5f74', fontSize: '0.85rem' }}>
                      {hols.map((h) => (
                        <li key={h.date}>{h.date} — {h.name} (skipped)</li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              );
            })()}

            <div className="info-card">
              <p>
                📅 Your report will be ready around{' '}
                <strong>{formatReadyDateTime(computeReportReady(form.endDate, form.arriveOfficeTime))}</strong>. After you
                submit, you&apos;ll get a <strong>reference ID</strong> and a link — bookmark it to check tracking status
                and view the final report any time.
              </p>
            </div>

            <label>
              MOH email
              <input
                type="email"
                required
                placeholder="name@moh.gov.my"
                pattern="[^@\s]+@([a-zA-Z0-9-]+\.)*moh\.gov\.my"
                title="Please use your Ministry of Health email (ending in moh.gov.my)."
                value={form.userEmail}
                onChange={(e) => setForm((current) => ({ ...current, userEmail: e.target.value }))}
              />
              <span className="muted" style={{ fontWeight: 400, fontSize: '0.8rem' }}>
                Used to confirm you&apos;re MOH staff and to count your weekly requests. No promotional emails are sent.
              </span>
            </label>

            {usage ? (
              <p className="muted" style={{ fontSize: '0.85rem', margin: 0 }}>
                You have used <strong>{usage.used}</strong> of {usage.limit} requests this week
                {usage.used >= usage.limit && usage.resetAt ? ` · resets ${formatShortDate(usage.resetAt)}` : ''}.
              </p>
            ) : null}

            {turnstileSiteKey ? <div ref={turnstileRef} className="mt-2" /> : null}

            <div className="mt-2">
              <button type="submit" className="w-full">Start tracking my commute</button>
            </div>

            {status ? <p className="success">{status}</p> : null}
            {savedRequestId ? (
              <a href={`/report/${savedRequestId}`} className="w-full">
                <button type="button" className="w-full">View tracking status &amp; report</button>
              </a>
            ) : null}
            {error ? <p className="error">{error}</p> : null}
          </form>
        </div>
      </section>

      {limitModal ? (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(15,33,58,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '1rem', zIndex: 50 }}
          onClick={() => setLimitModal(null)}
        >
          <div className="card" style={{ maxWidth: '440px' }} onClick={(e) => e.stopPropagation()}>
            <h2 style={{ marginTop: 0, color: '#b45309' }}>Free weekly limit reached</h2>
            <p style={{ color: '#4b5f74', lineHeight: 1.6 }}>
              This planner is free for MOH staff, so each user is limited to {WEEKLY_LIMIT} tracking
              requests per week to keep it within our free operating budget. You have used all {WEEKLY_LIMIT}.
            </p>
            <p style={{ color: '#16324f' }}>
              Please try again on <strong>{formatShortDate(limitModal.resetAt)}</strong>, when your weekly quota resets.
            </p>
            <button type="button" onClick={() => setLimitModal(null)}>Got it</button>
          </div>
        </div>
      ) : null}
    </main>
  );
}
