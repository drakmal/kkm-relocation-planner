-- Enable UUID extension
create extension if not exists "uuid-ossp";

-- Users table
create table if not exists public.users (
  id uuid primary key default uuid_generate_v4(),
  full_name text,
  email text unique not null,
  phone text,
  role text default 'health_worker',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Locations table (KKM/JKN/PKD/KK)
create table if not exists public.locations (
  id uuid primary key default uuid_generate_v4(),
  name text not null,
  tier text not null check (tier in ('ministry','state','district','clinic')),
  parent_id uuid references public.locations(id) on delete set null,
  latitude double precision,
  longitude double precision,
  address text,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- Support older installs that still use parent_location_id
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'locations'
      AND column_name = 'parent_location_id'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'locations'
      AND column_name = 'parent_id'
  ) THEN
    ALTER TABLE public.locations RENAME COLUMN parent_location_id TO parent_id;
  END IF;
END $$;

ALTER TABLE public.locations ADD COLUMN IF NOT EXISTS parent_id uuid references public.locations(id) on delete set null;

create index if not exists idx_locations_parent_id on public.locations(parent_id);
create index if not exists idx_locations_tier on public.locations(tier);
create index if not exists idx_locations_name on public.locations(name);

-- Tracking requests table
create table if not exists public.tracking_requests (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid references public.users(id) on delete set null,
  target_office_tier text not null check (target_office_tier in ('ministry','state','district','clinic')),
  target_office_name text not null,
  arrival_time time not null,
  return_time time not null,
  travel_mode text not null check (travel_mode in ('car','bike')),
  tracking_duration text not null,
  user_email text not null,
  status text not null default 'pending' check (status in ('pending','active','completed','cancelled')),
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz
);

create index if not exists idx_tracking_requests_status on public.tracking_requests(status);
create index if not exists idx_tracking_requests_created_at on public.tracking_requests(created_at desc);

-- Origin anchors table
create table if not exists public.origin_anchors (
  id uuid primary key default uuid_generate_v4(),
  tracking_request_id uuid references public.tracking_requests(id) on delete cascade,
  name text not null,
  latitude double precision not null,
  longitude double precision not null,
  radius_km double precision default 25,
  category text default 'residential',
  source text default 'overpass',
  created_at timestamptz not null default now()
);

create index if not exists idx_origin_anchors_request on public.origin_anchors(tracking_request_id);

-- Daily traffic logs table
create table if not exists public.daily_traffic_logs (
  id uuid primary key default uuid_generate_v4(),
  tracking_request_id uuid references public.tracking_requests(id) on delete cascade,
  origin_anchor_id uuid references public.origin_anchors(id) on delete cascade,
  log_date date not null,
  travel_time_minutes integer,
  weather_temp_c double precision,
  weather_condition text,
  weather_wind_kmh double precision,
  weather_precip_mm double precision,
  source text default 'google_maps_openmeteo',
  created_at timestamptz not null default now()
);

create index if not exists idx_daily_traffic_logs_date on public.daily_traffic_logs(log_date);
create index if not exists idx_daily_traffic_logs_request on public.daily_traffic_logs(tracking_request_id);

-- Optional analytics summary table
create table if not exists public.analysis_reports (
  id uuid primary key default uuid_generate_v4(),
  tracking_request_id uuid references public.tracking_requests(id) on delete cascade,
  summary_text text not null,
  generated_at timestamptz not null default now()
);

-- Row Level Security placeholders (ready for enablement)
-- alter table public.users enable row level security;
-- alter table public.tracking_requests enable row level security;
-- alter table public.daily_traffic_logs enable row level security;
