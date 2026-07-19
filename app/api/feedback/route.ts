import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || process.env.SUPABASE_URL || '';
const supabaseServiceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY || '';
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || process.env.SUPABASE_ANON_KEY || '';

const supabase = supabaseUrl && (supabaseServiceRoleKey || supabaseAnonKey)
  ? createClient(supabaseUrl, supabaseServiceRoleKey || supabaseAnonKey)
  : null;

const MAX_MESSAGE = 2000;
const MAX_FIELD = 300;
const IP_DAILY_LIMIT = 20; // light spam guard; feedback has no paid cost

function getClientIp(request: Request): string | null {
  const xff = request.headers.get('x-forwarded-for');
  if (xff) {
    const first = xff.split(',')[0].trim();
    if (first) return first;
  }
  const xr = request.headers.get('x-real-ip');
  return xr && xr.trim() ? xr.trim() : null;
}

// Best-effort daily cap per IP. Returns false (allow) if the lookup fails, e.g.
// the ip_address column is absent — never block a genuine report on infra.
async function overDailyLimit(ip: string): Promise<boolean> {
  const dayAgo = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
  const { count, error } = await supabase!
    .from('feedback')
    .select('id', { count: 'exact', head: true })
    .eq('ip_address', ip)
    .gte('created_at', dayAgo);
  if (error) return false;
  return (count || 0) >= IP_DAILY_LIMIT;
}

export async function POST(request: Request) {
  try {
    if (!supabase) {
      return NextResponse.json({ error: 'Supabase is not configured yet' }, { status: 500 });
    }

    const payload = await request.json().catch(() => ({}));
    const message = String(payload.message || '').trim();
    if (!message) {
      return NextResponse.json({ error: 'Please describe the issue or the facility you could not find.' }, { status: 400 });
    }

    const clientIp = getClientIp(request);
    if (clientIp && (await overDailyLimit(clientIp))) {
      return NextResponse.json({ error: 'Thanks — you have sent several reports today. Please try again tomorrow.' }, { status: 429 });
    }

    const row: Record<string, unknown> = {
      kind: ['missing_facility', 'issue', 'contact'].includes(payload.kind) ? payload.kind : 'issue',
      facility_query: payload.facilityQuery ? String(payload.facilityQuery).slice(0, MAX_FIELD) : null,
      message: message.slice(0, MAX_MESSAGE),
      user_email: payload.userEmail ? String(payload.userEmail).trim().slice(0, MAX_FIELD) : null,
      page_context: payload.pageContext && typeof payload.pageContext === 'object' ? payload.pageContext : null,
    };

    const { data, error } = await supabase.from('feedback').insert([row]).select('id').single();
    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    // Best-effort IP write (separate update so a missing column can't fail the insert).
    if (clientIp) {
      const { error: ipError } = await supabase.from('feedback').update({ ip_address: clientIp }).eq('id', data.id);
      if (ipError) {
        console.warn('[feedback] ip_address write failed (run db/004_add_feedback_table.sql?)', ipError.message);
      }
    }

    return NextResponse.json({ ok: true, id: data.id }, { status: 201 });
  } catch {
    return NextResponse.json({ error: 'Invalid request body' }, { status: 400 });
  }
}
