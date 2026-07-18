import { SupabaseClient } from '@supabase/supabase-js';

// Mirrors python_scripts/collect_traffic.py's get_target_office_coordinates()
// hardcoded shortcuts, kept in sync manually since both are small lookup tables.
const KNOWN_OFFICES: Record<string, [number, number]> = {
  'pkd sik': [5.8171, 100.7427],
  'kkm putrajaya': [2.9264, 101.6943],
  'jkn selangor': [3.0738, 101.5183],
  'pkd klang': [3.0408, 101.4473],
};

const OVERPASS_URL = 'https://overpass-api.de/api/interpreter';
const PLACE_TYPES = new Set(['village', 'town', 'suburb', 'hamlet']);
const EXCLUDED_LANDUSE = ['farm', 'farmland', 'orchard', 'forest', 'wood', 'scrub'];

type Anchor = {
  name: string;
  latitude: number;
  longitude: number;
  radius_km: number;
  category: string;
  source: string;
};

/**
 * Resolve a target office name/tier to coordinates: known shortcuts first,
 * then the locations table, then a Google Geocoding fallback. Mirrors
 * python_scripts/collect_traffic.py's get_target_office_coordinates().
 */
export async function resolveOfficeCoordinates(
  supabase: SupabaseClient,
  officeName: string,
  officeTier: string
): Promise<{ lat: number; lng: number } | null> {
  if (!officeName) return null;

  const normalized = officeName.trim().toLowerCase();
  for (const [knownName, [lat, lng]] of Object.entries(KNOWN_OFFICES)) {
    if (normalized.includes(knownName)) {
      return { lat, lng };
    }
  }

  let query = supabase.from('locations').select('latitude, longitude, name, tier');
  if (officeTier) query = query.eq('tier', officeTier);
  const { data: matches } = await query.ilike('name', `%${officeName}%`).limit(5);

  if (matches && matches.length > 0) {
    const first = matches[0] as { latitude: number; longitude: number };
    return { lat: Number(first.latitude), lng: Number(first.longitude) };
  }

  const googleApiKey = process.env.GOOGLE_MAPS_API_KEY;
  if (!googleApiKey) return null;

  const geocodeUrl = new URL('https://maps.googleapis.com/maps/api/geocode/json');
  geocodeUrl.searchParams.set('address', officeName);
  geocodeUrl.searchParams.set('key', googleApiKey);

  const response = await fetch(geocodeUrl.toString(), { signal: AbortSignal.timeout(10_000) });
  const geocodeData = await response.json();
  if (geocodeData.status !== 'OK') return null;

  const location = geocodeData.results[0].geometry.location;
  return { lat: location.lat, lng: location.lng };
}

/**
 * Query OpenStreetMap Overpass for nearby villages/towns/suburbs/hamlets to
 * use as candidate residential anchors. Mirrors find_anchors.py's
 * fetch_residential_anchors().
 */
export async function fetchResidentialAnchors(
  lat: number,
  lng: number,
  radiusKm = 25
): Promise<Anchor[]> {
  const query = `
    [out:json][timeout:25];
    (
      node["place"~"^(village|town|suburb|hamlet)$"](around:${radiusKm * 1000},${lat},${lng});
      way["place"~"^(village|town|suburb|hamlet)$"](around:${radiusKm * 1000},${lat},${lng});
      relation["place"~"^(village|town|suburb|hamlet)$"](around:${radiusKm * 1000},${lat},${lng});
    );
    out center;
  `;

  const response = await fetch(OVERPASS_URL, {
    method: 'POST',
    headers: {
      'User-Agent': 'KKM-Relocation-Planner/1.0',
      Accept: 'application/json',
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: new URLSearchParams({ data: query }).toString(),
    signal: AbortSignal.timeout(20_000),
  });

  if (!response.ok) {
    throw new Error(`Overpass API request failed: ${response.status}`);
  }

  const data = await response.json();
  const elements: Array<Record<string, any>> = data.elements || [];

  const seen = new Set<string>();
  const anchors: Anchor[] = [];

  for (const element of elements) {
    const tags = element.tags || {};
    const name: string = tags.name || tags.place || 'Unnamed';
    const placeType: string = tags.place || '';

    const landuse = (tags.landuse || '').toLowerCase();
    if (EXCLUDED_LANDUSE.some((keyword) => landuse.includes(keyword))) continue;
    if (!PLACE_TYPES.has(placeType)) continue;

    let elLat = element.lat;
    let elLng = element.lon;
    if (elLat == null || elLng == null) {
      if (element.center) {
        elLat = element.center.lat;
        elLng = element.center.lon;
      }
    }
    if (elLat == null || elLng == null) continue;

    const key = `${elLat.toFixed(5)},${elLng.toFixed(5)}`;
    if (seen.has(key)) continue;
    seen.add(key);

    anchors.push({
      name,
      latitude: Number(elLat.toFixed(6)),
      longitude: Number(elLng.toFixed(6)),
      radius_km: radiusKm,
      category: placeType,
      source: 'overpass',
    });
  }

  return anchors;
}

/** Insert anchors into origin_anchors for a tracking request. */
export async function saveAnchors(
  supabase: SupabaseClient,
  trackingRequestId: string,
  anchors: Anchor[]
) {
  if (anchors.length === 0) return;

  const rows = anchors.map((anchor) => ({
    tracking_request_id: trackingRequestId,
    name: anchor.name,
    latitude: anchor.latitude,
    longitude: anchor.longitude,
    radius_km: anchor.radius_km,
    category: anchor.category,
    source: anchor.source,
  }));

  const { error } = await supabase.from('origin_anchors').insert(rows);
  if (error) {
    throw new Error(`Failed to save origin anchors: ${error.message}`);
  }
}

/**
 * Full pipeline: resolve the office's coordinates, fetch nearby residential
 * anchors, and save them. Returns the number of anchors saved (0 if the
 * office couldn't be geolocated).
 */
export async function createAnchorsForRequest(
  supabase: SupabaseClient,
  trackingRequestId: string,
  officeName: string,
  officeTier: string
): Promise<number> {
  const coords = await resolveOfficeCoordinates(supabase, officeName, officeTier);
  if (!coords) return 0;

  const anchors = await fetchResidentialAnchors(coords.lat, coords.lng);
  await saveAnchors(supabase, trackingRequestId, anchors);
  return anchors.length;
}
