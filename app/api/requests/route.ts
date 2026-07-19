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

// Per-IP cap. The email gate only *format*-checks the address, so a fake
// @moh.gov.my passes and each fake email gets its own 3/week quota. Capping by
// source IP bounds paid Distance Matrix usage regardless of how many emails an
// abuser invents. Set a bit above the email cap so a small shared office (a few
// colleagues behind one NAT) is unlikely to trip it in normal use.
const IP_WEEKLY_LIMIT = 5;

// Cloudflare Turnstile CAPTCHA. Blocks scripted abuse that would otherwise
// invent many @moh.gov.my emails to run up paid Distance Matrix usage. Gated on
// TURNSTILE_SECRET_KEY: if the secret is not set the check is skipped, so the
// app keeps working before the keys are provisioned and activates once they are.
const TURNSTILE_SECRET_KEY = process.env.TURNSTILE_SECRET_KEY || '';

async function verifyTurnstile(token: unknown, ip: string | null): Promise<boolean> {
  if (!TURNSTILE_SECRET_KEY) return true; // not configured -> gate disabled
  if (typeof token !== 'string' || !token) return false;
  try {
    const body = new URLSearchParams();
    body.append('secret', TURNSTILE_SECRET_KEY);
    body.append('response', token);
    if (ip) body.append('remoteip', ip);
    const res = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
      method: 'POST',
      body,
    });
    const data = await res.json();
    return !!data.success;
  } catch (err) {
    console.warn('[requests] turnstile verification error', err);
    return false;
  }
}

// Best-effort client IP. On Vercel the real client is the first hop in
// x-forwarded-for; x-real-ip is a fallback. Returns null if neither is present.
function getClientIp(request: Request): string | null {
  const xff = request.headers.get('x-forwarded-for');
  if (xff) {
    const first = xff.split(',')[0].trim();
    if (first) return first;
  }
  const xr = request.headers.get('x-real-ip');
  if (xr && xr.trim()) return xr.trim();
  return null;
}

// Requests made from this IP in the rolling 7-day window. Returns null if the
// lookup fails (e.g. the ip_address column has not been migrated yet), which
// the caller treats as "IP gate disabled" so submissions still work.
async function ipWeeklyUsage(ip: string): Promise<{ used: number; resetAt: string | null } | null> {
  const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
  const { data, error } = await supabase!
    .from('tracking_requests')
    .select('created_at')
    .eq('ip_address', ip)
    .gte('created_at', weekAgo)
    .order('created_at', { ascending: true });
  if (error) return null;
  const rows = data || [];
  const resetAt = rows.length
    ? new Date(new Date(rows[0].created_at).getTime() + 7 * 24 * 60 * 60 * 1000).toISOString()
    : null;
  return { used: rows.length, resetAt };
}

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
    const clientIp = getClientIp(request);

    // CAPTCHA: verify the Turnstile token before any DB work or paid pipeline.
    // No-op until TURNSTILE_SECRET_KEY is set (see verifyTurnstile).
    const captchaOk = await verifyTurnstile(payload.turnstileToken, clientIp);
    if (!captchaOk) {
      return NextResponse.json(
        { error: 'CAPTCHA verification failed. Please complete the challenge and try again.' },
        { status: 400 },
      );
    }

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

    // Second guardrail: cap requests per source IP so an abuser who invents many
    // fake @moh.gov.my emails still cannot drive unbounded paid Distance Matrix
    // usage from one machine. Best-effort — if the ip_address column is not
    // migrated yet, ipWeeklyUsage returns null and the gate is skipped.
    if (clientIp) {
      const ipUsage = await ipWeeklyUsage(clientIp);
      if (ipUsage && ipUsage.used >= IP_WEEKLY_LIMIT) {
        return NextResponse.json(
          {
            error: 'Too many requests from your network this week. Please try again after your weekly quota resets.',
            resetAt: ipUsage.resetAt,
            limitReached: true,
          },
          { status: 429 },
        );
      }
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

    // Best-effort: record the source IP for the per-IP weekly cap. Done as a
    // separate update (not part of the insert above) so the submission still
    // succeeds if the ip_address column has not been migrated yet.
    if (clientIp) {
      const { error: ipError } = await supabase
        .from('tracking_requests')
        .update({ ip_address: clientIp })
        .eq('id', data.id);
      if (ipError) {
        console.warn('[requests] ip_address write failed (run db/003_add_ip_rate_limit.sql?)', ipError.message);
      }
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
