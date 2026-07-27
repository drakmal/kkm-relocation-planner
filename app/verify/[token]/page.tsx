'use client';

import { useEffect, useState } from 'react';

type PeekResponse = {
  status: 'valid' | 'expired' | 'not_found';
  email?: string;
  officeName?: string;
};

type ConfirmResponse = {
  id?: string;
  emailed?: boolean;
  error?: string;
  resetAt?: string | null;
};

function formatShortDate(value?: string | null): string {
  if (!value) return 'the next weekly reset';
  return new Date(value).toLocaleString('en-MY', { dateStyle: 'medium', timeStyle: 'short' });
}

export default function VerifyPage({ params }: { params: { token: string } }) {
  const { token } = params;
  const [loading, setLoading] = useState(true);
  const [peek, setPeek] = useState<PeekResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [confirming, setConfirming] = useState(false);
  const [confirmed, setConfirmed] = useState<{ id: string; emailed: boolean } | null>(null);
  const [confirmError, setConfirmError] = useState<{ message: string; resetAt?: string | null } | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const response = await fetch(`/api/verify/${token}`);
        const payload = await response.json();
        if (!cancelled) {
          if (!response.ok && response.status !== 404) {
            throw new Error(payload.error || 'Unable to check this confirmation link.');
          }
          setPeek(payload as PeekResponse);
        }
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : 'Unexpected error');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function handleConfirm() {
    setConfirming(true);
    setConfirmError(null);
    try {
      const response = await fetch(`/api/verify/${token}`, { method: 'POST' });
      const payload = (await response.json()) as ConfirmResponse;
      if (!response.ok) {
        setConfirmError({ message: payload.error || 'Unable to confirm this request.', resetAt: payload.resetAt });
        return;
      }
      setConfirmed({ id: payload.id!, emailed: Boolean(payload.emailed) });
    } catch (err) {
      setConfirmError({ message: err instanceof Error ? err.message : 'Unexpected error' });
    } finally {
      setConfirming(false);
    }
  }

  return (
    <main className="min-h-screen w-full p-4">
      <section className="card p-4 gap-4 flex flex-col w-full max-w-3xl mx-auto">
        <div className="hero-banner py-2 px-3 gap-3 flex items-center justify-between flex-wrap">
          <div className="min-w-0">
            <p className="eyebrow">KKM Relocation Planner</p>
            <h1 className="text-lg font-semibold leading-tight">Confirm your tracking request</h1>
          </div>
          <a href="/" className="helper text-white font-medium text-sm m-0 whitespace-nowrap underline">
            &larr; New request
          </a>
        </div>

        {loading ? <p className="muted">Checking your confirmation link…</p> : null}
        {loadError ? <p className="error">{loadError}</p> : null}

        {!loading && !loadError && peek?.status === 'not_found' ? (
          <div className="info-card">
            <p>
              This confirmation link is invalid or has already been used. If you still want to track
              your commute, please <a href="/" className="underline">submit a new request</a>.
            </p>
          </div>
        ) : null}

        {!loading && !loadError && peek?.status === 'expired' ? (
          <div className="info-card">
            <p>
              This confirmation link has expired (links are valid for 30 minutes). Please{' '}
              <a href="/" className="underline">submit a new request</a> — no quota was used by the
              expired one.
            </p>
          </div>
        ) : null}

        {!loading && !loadError && peek?.status === 'valid' && !confirmed ? (
          <>
            <div className="info-card">
              <p>
                We&apos;ve sent a confirmation link to <strong>{peek.email}</strong> — click below to
                confirm this is your request and start tracking your commute to{' '}
                <strong>{peek.officeName}</strong>.
              </p>
              <p className="muted" style={{ fontSize: '0.8rem', marginTop: '0.4rem' }}>
                If you don&apos;t see this arriving from elsewhere, check your spam/junk folder — this is
                a newly-verified sending domain.
              </p>
            </div>

            <button type="button" className="w-full" onClick={handleConfirm} disabled={confirming}>
              {confirming ? 'Confirming…' : 'Confirm and start tracking'}
            </button>

            {confirmError ? (
              <p className="error">
                {confirmError.message}
                {confirmError.resetAt ? ` (resets ${formatShortDate(confirmError.resetAt)})` : ''}
              </p>
            ) : null}
          </>
        ) : null}

        {confirmed ? (
          <div className="info-card">
            <p>
              Tracking started. Your reference ID:{' '}
              <strong className="break-words">{confirmed.id}</strong>
              {confirmed.emailed ? ' (also emailed to you).' : '.'}
            </p>
            <a href={`/report/${confirmed.id}`} className="w-full" style={{ display: 'block', marginTop: '0.6rem' }}>
              <button type="button" className="w-full">View tracking status &amp; report</button>
            </a>
          </div>
        ) : null}
      </section>
    </main>
  );
}
