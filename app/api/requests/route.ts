import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';
import { createAnchorsForRequest } from '@/app/lib/originAnchors';
import { workingDaysBetween } from '@/app/lib/holidays';
import { sendConfirmationEmail } from '@/app/lib/email';

const MAX_WORKING_DAYS = 5;

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || process.env.SUPABASE_URL || '';
const supabaseServiceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY || '';
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || process.env.SUPABASE_ANON_KEY || '';

// Service role is required so this route can also write to origin_anchors
// (see createAnchorsForRequest below), not just tracking_requests.
const supabase = supabaseUrl && (supabaseServiceRoleKey || supabaseAnonKey)
  ? createClient(supabaseUrl, supabaseServiceRoleKey || supabaseAnonKey)
  : null;

// Each tracking request triggers ongoing paid Google Distance Matrix calls, so
// access is limited to Ministry of Health staff. Accepts @moh.gov.my and any
// *.moh.gov.my sub-domain (e.g. state JKN mail servers).
const ALLOWED_EMAIL_DOMAIN = 'moh.gov.my';
function isMohEmail(email: string): boolean {
  const match = /^[^@\s]+@([^@\s]+)$/.exec((email || '').trim().toLowerCase());
  if (!match) return false;
  const domain = match[1];
  return domain === ALLOWED_EMAIL_DOMAIN || domain.endsWith('.' + ALLOWED_EMAIL_DOMAIN);
}

const WEEKLY_LIMIT = 3;

// Requests made by this email in the rolling 7-day window, plus when the quota
// resets (7 days after the oldest request in the window).
async function weeklyUsage(email: string): Promise<{ used: number; limit: number; resetAt: string | null }> {
  const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
  const { data } = await supabase!
    .from('tracking_requests')
    .select('created_at')
    .eq('user_email', email)
    .gte('created_at', weekAgo)
    .order('created_at', { ascending: true });
  const rows = data || [];
  const resetAt = rows.length
    ? new Date(new Date(rows[0].created_at).getTime() + 7 * 24 * 60 * 60 * 1000).toISOString()
    : null;
  return { used: rows.length, limit: WEEKLY_LIMIT, resetAt };
}

// GET /api/requests?email=... -> current weekly usage for the email.
export async function GET(request: Request) {
  if (!supabase) {
    return NextResponse.json({ error: 'Supabase is not configured yet' }, { status: 500 });
  }
  const email = new URL(request.url).searchParams.get('email') || '';
  if (!isMohEmail(email)) {
    return NextResponse.json({ error: 'A valid moh.gov.my email is required.' }, { status: 400 });
  }
  const usage = await weeklyUsage(email.trim().toLowerCase());
  return NextResponse.json(usage);
}

export async function POST(request: Request) {
  try {
    if (!supabase) {
      return NextResponse.json({ error: 'Supabase is not configured yet' }, { status: 500 });
    }

    const payload = await request.json();

    if (!isMohEmail(payload.userEmail)) {
      return NextResponse.json(
        { error: 'This planner is for Ministry of Health staff only. Please register with your @moh.gov.my email address.' },
        { status: 403 },
      );
    }

    // Validate the tracking window server-side (defence in depth for the 5-day cap).
    const workingDays = workingDaysBetween(payload.startDate, payload.endDate);
    if (workingDays < 1) {
      return NextResponse.json({ error: 'Invalid tracking period.' }, { status: 400 });
    }
    if (workingDays > MAX_WORKING_DAYS) {
      return NextResponse.json(
        { error: `Tracking period exceeds the ${MAX_WORKING_DAYS}-working-day limit.` },
        { status: 400 },
      );
    }

    const email = String(payload.userEmail).trim().toLowerCase();

    // Rate limit: at most 3 tracking requests per email in a rolling 7 days.
    // Each request drives ongoing paid Distance Matrix usage, so this caps the
    // blast radius even for a valid MOH user.
    const usage = await weeklyUsage(email);
    if (usage.used >= WEEKLY_LIMIT) {
      return NextResponse.json(
        {
          error: `You have used all ${WEEKLY_LIMIT} of your weekly requests.`,
          resetAt: usage.resetAt,
          limitReached: true,
        },
        { status: 429 },
      );
    }

    const { data, error } = await supabase.from('tracking_requests').insert([
      {
        target_office_tier: payload.targetOfficeTier,
        target_office_name: payload.targetOfficeName,
        arrival_time: payload.arrivalTime,
        return_time: payload.returnTime,
        travel_mode: payload.travelMode,
        tracking_duration: payload.trackingDuration,
        user_email: email,
        created_at: payload.created_at,
      },
    ]).select().single();

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    // Best-effort: find residential anchors around the target office so daily
    // traffic collection has somewhere to measure from. The tracking request
    // is already saved, so a failure here must not fail the whole submission.
    try {
      const anchorCount = await createAnchorsForRequest(
        supabase,
        data.id,
        payload.targetOfficeName,
        payload.targetOfficeTier
      );
      if (anchorCount === 0) {
        console.warn(`[requests] no anchors created for "${payload.targetOfficeName}" (could not resolve coordinates)`);
      }
    } catch (anchorError) {
      console.warn('[requests] anchor creation failed', anchorError);
    }

    // Best-effort confirmation email carrying the reference ID (in case the
    // user forgets it). Never block the submission on email delivery.
    let emailed = false;
    try {
      emailed = await sendConfirmationEmail({
        to: email,
        refId: data.id,
        officeName: payload.targetOfficeName || 'your target office',
        reportReadyText: payload.reportReadyText,
      });
    } catch (mailError) {
      console.warn('[requests] confirmation email failed', mailError);
    }

    return NextResponse.json({ id: data.id, emailed, message: 'Tracking request saved' }, { status: 201 });
  } catch (error) {
    return NextResponse.json({ error: 'Invalid request body' }, { status: 400 });
  }
}
