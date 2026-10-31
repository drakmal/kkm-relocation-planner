import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';
import { sendConfirmationEmail } from '@/app/lib/email';
import { weeklyUsage, ipWeeklyUsage, WEEKLY_LIMIT, IP_WEEKLY_LIMIT } from '@/app/api/requests/route';

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || process.env.SUPABASE_URL || '';
const supabaseServiceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY || '';
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || process.env.SUPABASE_ANON_KEY || '';

const supabase = supabaseUrl && (supabaseServiceRoleKey || supabaseAnonKey)
  ? createClient(supabaseUrl, supabaseServiceRoleKey || supabaseAnonKey)
  : null;

type PendingRow = {
  id: string;
  token: string;
  email: string;
  ip_address: string | null;
  payload: Record<string, any>;
  created_at: string;
  expires_at: string;
  confirmed_at: string | null;
};

async function findPending(token: string): Promise<{ row: PendingRow | null; error: string | null }> {
  const { data, error } = await supabase!
    .from('pending_verifications')
    .select('*')
    .eq('token', token)
    .maybeSingle();
  if (error) return { row: null, error: error.message };
  return { row: (data as PendingRow) || null, error: null };
}

// GET /api/verify/[token] -> read-only peek, safe for email-scanner pre-fetch
// (Microsoft 365/Exchange Safe Links-style scanners auto-fetch links in
// incoming mail before a human opens them; a mutating GET would let the
// scanner silently burn the token before the real user ever clicks).
export async function GET(_request: Request, { params }: { params: Promise<{ token: string }> }) {
  if (!supabase) {
    return NextResponse.json({ error: 'Supabase is not configured yet' }, { status: 500 });
  }

  const { token } = await params;
  const { row, error } = await findPending(token);
  if (error) {
    return NextResponse.json({ error }, { status: 500 });
  }
  if (!row) {
    return NextResponse.json({ status: 'not_found' }, { status: 404 });
  }
  if (new Date(row.expires_at).getTime() < Date.now()) {
    return NextResponse.json({ status: 'expired' });
  }

  return NextResponse.json({
    status: 'valid',
    email: row.email,
    officeName: row.payload?.targetOfficeName || 'your target office',
  });
}

// POST /api/verify/[token] -> the actual mutating confirm, fired only by an
// explicit button click on the interstitial page (see
// app/verify/[token]/page.tsx). This is where the real tracking_requests row
// is created and the weekly/IP quota is spent.
export async function POST(_request: Request, { params }: { params: Promise<{ token: string }> }) {
  if (!supabase) {
    return NextResponse.json({ error: 'Supabase is not configured yet' }, { status: 500 });
  }

  const { token } = await params;
  const { row, error } = await findPending(token);
  if (error) {
    return NextResponse.json({ error }, { status: 500 });
  }
  if (!row) {
    return NextResponse.json({ error: 'This confirmation link is invalid or has already been used.' }, { status: 404 });
  }
  if (new Date(row.expires_at).getTime() < Date.now()) {
    return NextResponse.json({ error: 'This confirmation link has expired. Please submit a new request.' }, { status: 410 });
  }

  // Atomically claim the row before doing any further work. Without this, a
  // double-click, a network retry, or the same link opened in two tabs can
  // both pass the findPending check above before either finishes, producing
  // two tracking_requests rows (and two confirmation emails) from a single
  // confirm intent. The conditional update below only succeeds for whichever
  // request gets there first; a concurrent second request gets null back and
  // is rejected instead of proceeding.
  const { data: claimed } = await supabase
    .from('pending_verifications')
    .update({ confirmed_at: new Date().toISOString() })
    .eq('id', row.id)
    .is('confirmed_at', null)
    .select()
    .maybeSingle();
  if (!claimed) {
    return NextResponse.json({ error: 'This confirmation link has already been used.' }, { status: 409 });
  }

  // Re-check the quota against tracking_requests (not the pending table) at
  // confirm-time. Accurate precisely because unconfirmed rows were never
  // written to tracking_requests: e.g. two pending links for the same email
  // could otherwise both have looked "under quota" at submit-time.
  const email = row.email;
  const usage = await weeklyUsage(email);
  if (usage.used >= WEEKLY_LIMIT) {
    return NextResponse.json(
      { error: `You have used all ${WEEKLY_LIMIT} of your weekly requests.`, resetAt: usage.resetAt, limitReached: true },
      { status: 429 },
    );
  }
  if (row.ip_address) {
    const ipUsage = await ipWeeklyUsage(row.ip_address);
    if (ipUsage && ipUsage.used >= IP_WEEKLY_LIMIT) {
      return NextResponse.json(
        { error: 'Too many requests from your network this week. Please try again after your weekly quota resets.', resetAt: ipUsage.resetAt, limitReached: true },
        { status: 429 },
      );
    }
  }

  const payload = row.payload || {};
  // created_at is the confirmation moment, not the original submission time:
  // using the submission time would let someone who submits near a weekly
  // reset but confirms just after it still count against the prior week.
  const { data, error: insertError } = await supabase
    .from('tracking_requests')
    .insert([
      {
        target_office_tier: payload.targetOfficeTier,
        target_office_name: payload.targetOfficeName,
        arrival_time: payload.arrivalTime,
        return_time: payload.returnTime,
        travel_mode: payload.travelMode,
        tracking_duration: payload.trackingDuration,
        user_email: email,
        created_at: new Date().toISOString(),
      },
    ])
    .select()
    .single();

  if (insertError) {
    return NextResponse.json({ error: insertError.message }, { status: 500 });
  }

  // Best-effort: record the source IP captured at *submission* time (not the
  // device used to click the confirmation link), mirroring the original
  // POST /api/requests backfill so the IP cap still measures the submitting
  // network. Skipped if the ip_address column has not been migrated yet.
  if (row.ip_address) {
    const { error: ipError } = await supabase
      .from('tracking_requests')
      .update({ ip_address: row.ip_address })
      .eq('id', data.id);
    if (ipError) {
      console.warn('[verify] ip_address write failed (run db/003_add_ip_rate_limit.sql?)', ipError.message);
    }
  }

  let emailed = false;
  try {
    emailed = await sendConfirmationEmail({
      to: email,
      refId: data.id,
      officeName: payload.targetOfficeName || 'your target office',
      reportReadyText: payload.reportReadyText,
    });
  } catch (mailError) {
    console.warn('[verify] confirmation email failed', mailError);
  }

  // Its job is done -- no need to wait for the daily cleanup job.
  await supabase.from('pending_verifications').delete().eq('id', row.id);

  return NextResponse.json(
    { id: data.id, emailed, reportReadyText: payload.reportReadyText || null, message: 'Tracking request confirmed' },
    { status: 201 },
  );
}
