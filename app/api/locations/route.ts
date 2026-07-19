import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || process.env.SUPABASE_URL || '';
const supabaseServiceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY || '';
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || process.env.SUPABASE_ANON_KEY || '';

const supabase = supabaseUrl && (supabaseServiceRoleKey || supabaseAnonKey)
  ? createClient(supabaseUrl, supabaseServiceRoleKey || supabaseAnonKey)
  : null;

// Classify a row into the facility categories the form's 5 boxes filter on.
function deriveCategory(row: {
  name: string;
  tier: string;
  metadata?: { category?: string } | null;
}): string {
  if (row.metadata?.category) return row.metadata.category;
  const name = (row.name || '').toLowerCase();
  const tier = (row.tier || '').toLowerCase();
  if (tier === 'state') return 'jkn';
  if (tier === 'ministry') return 'other';
  if (tier === 'district') {
    if (name.startsWith('pkpd ')) return 'pkpd';
    if (name.startsWith('pkd ')) return 'pkd';
    return 'hospital';
  }
  if (tier === 'clinic') {
    if (name.includes('klinik desa') || name.startsWith('kd ')) return 'klinik_desa';
    if (name.includes('pergigian') || name.startsWith('kp ')) return 'klinik_pergigian';
    if (name.includes('1malaysia') || name.includes('1 malaysia')) return 'klinik_1malaysia';
    return 'klinik_kesihatan';
  }
  return 'other';
}

function sortLocations(items: Array<{ name: string }>) {
  return [...items].sort((a, b) => {
    const aIsHospital = /hospital/i.test(a.name);
    const bIsHospital = /hospital/i.test(b.name);
    if (aIsHospital !== bIsHospital) {
      return aIsHospital ? -1 : 1;
    }

    const aIsPkd = /\bpkd\b/i.test(a.name);
    const bIsPkd = /\bpkd\b/i.test(b.name);
    if (aIsPkd !== bIsPkd) {
      return aIsPkd ? -1 : 1;
    }

    return a.name.localeCompare(b.name, 'ms-MY');
  });
}

export async function GET(request: Request) {
  try {
    if (!supabase) {
      return NextResponse.json({ error: 'Supabase is not configured yet' }, { status: 500 });
    }

    const { searchParams } = new URL(request.url);
    const parentId = searchParams.get('parent_id');
    const mode = searchParams.get('mode') || '';

    const SELECT = 'id, name, tier, parent_location_id, latitude, longitude, metadata';
    const normalize = (row: {
      id: string; name: string; tier: string;
      parent_location_id?: string | null;
      latitude?: number | null; longitude?: number | null;
      metadata?: { category?: string } | null;
    }) => ({
      id: row.id,
      name: row.name,
      tier: row.tier,
      parent_id: row.parent_location_id ?? null,
      latitude: row.latitude ?? null,
      longitude: row.longitude ?? null,
      category: deriveCategory(row),
    });

    // Box 5 (Others): static MOH HQ / NIH plus any ministry-tier roots.
    if (mode === 'other') {
      const { data, error } = await supabase
        .from('locations').select(SELECT)
        .eq('tier', 'ministry').is('parent_location_id', null).limit(5000);
      if (error) return NextResponse.json({ error: error.message }, { status: 500 });
      const staticOther = [
        // MOH Headquarters — Kompleks E, Parcel E, Presint 1, Putrajaya.
        { id: 'other-moh-hq', name: 'MOH HQ', tier: 'ministry', parent_id: null, latitude: 2.9416956, longitude: 101.7070911, category: 'other' },
        // National Institute of Health (Institut Kesihatan Negara) — Setia Alam, Shah Alam, Selangor.
        { id: 'other-nih', name: 'NIH', tier: 'ministry', parent_id: null, latitude: 3.1088187, longitude: 101.4506716, category: 'other' },
      ];
      return NextResponse.json(sortLocations([...staticOther, ...(data || []).map(normalize)]));
    }

    // Filter by parent SERVER-SIDE. Fetching the whole table and filtering in
    // JS silently capped at Supabase's 1000-row default and dropped facilities.
    let query = supabase.from('locations').select(SELECT).limit(5000);
    if (parentId) {
      query = query.eq('parent_location_id', parentId);
    } else {
      // Box 1 (JKN): state/ministry-tier roots only.
      query = query.is('parent_location_id', null).in('tier', ['state', 'ministry']);
    }

    const { data, error } = await query.order('name', { ascending: true });
    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }
    console.log('[locations api]', { parentId, mode, count: (data || []).length });

    return NextResponse.json(sortLocations((data || []).map(normalize)));
  } catch (error) {
    return NextResponse.json({ error: 'Unable to load locations' }, { status: 500 });
  }
}
