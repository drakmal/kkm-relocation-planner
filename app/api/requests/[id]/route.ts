import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || process.env.SUPABASE_URL || '';
const supabaseServiceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY || '';
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || process.env.SUPABASE_ANON_KEY || '';

const supabase = supabaseUrl && (supabaseServiceRoleKey || supabaseAnonKey)
  ? createClient(supabaseUrl, supabaseServiceRoleKey || supabaseAnonKey)
  : null;

export async function GET(_request: Request, { params }: { params: { id: string } }) {
  try {
    if (!supabase) {
      return NextResponse.json({ error: 'Supabase is not configured yet' }, { status: 500 });
    }

    const { id } = params;
    if (!id) {
      return NextResponse.json({ error: 'Missing request id' }, { status: 400 });
    }

    // select('*') keeps this resilient: it works before AND after the
    // report columns migration has been applied (missing columns are simply
    // absent from the payload rather than causing a query error).
    const { data: requestRow, error: requestError } = await supabase
      .from('tracking_requests')
      .select('*')
      .eq('id', id)
      .maybeSingle();

    if (requestError) {
      return NextResponse.json({ error: requestError.message }, { status: 500 });
    }

    if (!requestRow) {
      return NextResponse.json({ error: 'Tracking request not found' }, { status: 404 });
    }

    // How many daily logs have been collected so far (progress indicator).
    const { count: logCount, error: logError } = await supabase
      .from('daily_traffic_logs')
      .select('*', { count: 'exact', head: true })
      .eq('tracking_request_id', id);

    if (logError) {
      // Don't fail the whole request just because the count failed.
      console.warn('[requests/[id]] log count failed', logError.message);
    }

    // Candidate residential areas (origin anchors) for the rental section.
    // De-duplicated by name; the report's AI ranking narrows these further.
    const { data: anchorRows } = await supabase
      .from('origin_anchors')
      .select('name, latitude, longitude')
      .eq('tracking_request_id', id)
      .limit(300);

    const seenArea = new Set<string>();
    const areas: Array<{ name: string; lat: number; lng: number }> = [];
    for (const a of anchorRows || []) {
      if (seenArea.has(a.name)) continue;
      seenArea.add(a.name);
      areas.push({ name: a.name, lat: a.latitude, lng: a.longitude });
      if (areas.length >= 12) break;
    }

    return NextResponse.json({
      request: requestRow,
      logCount: logCount ?? 0,
      areas,
    });
  } catch (error) {
    return NextResponse.json({ error: 'Unable to load tracking request' }, { status: 500 });
  }
}
