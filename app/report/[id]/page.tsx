'use client';

import { useEffect, useState } from 'react';
import { rentalLinksFor } from '@/app/lib/rental';

type TrackingRequest = {
  id: string;
  target_office_name?: string | null;
  target_office_tier?: string | null;
  arrival_time?: string | null; // arrive-office time
  return_time?: string | null;  // repurposed: leave-home time
  tracking_duration?: string | null;
  user_email?: string | null;
  created_at?: string | null;
  status?: string | null;
  report_text?: string | null;
  report_generated_at?: string | null;
};

type Area = { name: string; lat: number; lng: number };

type ApiResponse = {
  request: TrackingRequest;
  logCount: number;
  areas?: Area[];
};

function formatDate(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('en-MY', { dateStyle: 'medium', timeStyle: 'short' });
}

function formatDuration(value?: string | null) {
  if (!value) return '—';
  return value.replace(/_/g, ' ');
}

function formatTime(value?: string | null) {
  if (!value) return '—';
  return value.slice(0, 5); // "07:30:00" -> "07:30"
}

export default function ReportPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const [data, setData] = useState<ApiResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const response = await fetch(`/api/requests/${id}`);
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || 'Unable to load tracking request');
        }
        if (!cancelled) {
          setData(payload as ApiResponse);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Unexpected error');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [id]);

  const request = data?.request;
  const hasReport = Boolean(request?.report_text);
  const status = request?.status || (hasReport ? 'completed' : 'pending');

  return (
    <main className="min-h-screen w-full p-4">
      <section className="card p-4 gap-4 flex flex-col w-full max-w-3xl mx-auto">
        <div className="hero-banner py-2 px-3 gap-3 flex items-center justify-between flex-wrap">
          <div className="min-w-0">
            <p className="eyebrow">KKM Relocation Planner</p>
            <h1 className="text-lg font-semibold leading-tight">Tracking status &amp; relocation report</h1>
          </div>
          <a href="/" className="helper text-white font-medium text-sm m-0 whitespace-nowrap underline">
            &larr; New request
          </a>
        </div>

        {loading ? <p className="muted">Loading tracking request…</p> : null}
        {error ? <p className="error">{error}</p> : null}

        {request ? (
          <>
            <div className="selected-office">
              <span className="selected-badge">
                {status === 'completed' ? 'Report ready' : 'Collecting data'}
              </span>
              <strong>{request.target_office_name || 'Unknown office'}</strong>
              <span className="muted">Tier: {request.target_office_tier || '—'}</span>
              {request.return_time || request.arrival_time ? (
                <span className="muted">
                  🏠 Leave home {formatTime(request.return_time)} → 🏢 Arrive office {formatTime(request.arrival_time)}
                </span>
              ) : null}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="info-card">
                <h2>Reference ID</h2>
                <p className="break-words">{request.id}</p>
              </div>
              <div className="info-card">
                <h2>Tracking duration</h2>
                <p>{formatDuration(request.tracking_duration)}</p>
              </div>
              <div className="info-card">
                <h2>Submitted</h2>
                <p>{formatDate(request.created_at)}</p>
              </div>
              <div className="info-card">
                <h2>Days of data collected</h2>
                <p>{data?.logCount ?? 0}</p>
              </div>
            </div>

            <div className="section-heading">
              <h2>Relocation report</h2>
              <p>
                {hasReport
                  ? `Generated ${formatDate(request.report_generated_at)}.`
                  : 'Your report will appear here automatically once the tracking period finishes and enough traffic data has been collected.'}
              </p>
            </div>

            {hasReport ? (
              <pre className="report-body">{request.report_text}</pre>
            ) : (
              <div className="info-card">
                <p>
                  Status: <strong>{status}</strong>. Check back after the tracking window
                  ({formatDuration(request.tracking_duration)}) has elapsed. We collect one data
                  point per working day.
                </p>
              </div>
            )}

            {data?.areas && data.areas.length > 0 ? (
              <>
                <div className="section-heading">
                  <h2>Rental options near recommended areas</h2>
                  <p>
                    Live rental listings for the residential areas within commuting range of your
                    target office. Current prices are shown on each portal.
                  </p>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {data.areas.map((area) => {
                    const links = rentalLinksFor(area.name);
                    return (
                      <div key={area.name} className="info-card">
                        <h2 style={{ fontSize: '0.95rem' }}>{area.name}</h2>
                        <p style={{ display: 'flex', gap: '0.7rem', flexWrap: 'wrap' }}>
                          <a href={links.mudah} target="_blank" rel="noopener noreferrer" className="underline">
                            Mudah.my &#8599;
                          </a>
                          <a href={links.iproperty} target="_blank" rel="noopener noreferrer" className="underline">
                            iProperty &#8599;
                          </a>
                        </p>
                      </div>
                    );
                  })}
                </div>
              </>
            ) : null}
          </>
        ) : null}
      </section>
    </main>
  );
}
