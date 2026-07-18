# KKM Relocation Planner

## Overview
This workspace contains:
- a Next.js frontend form for tracking requests
- a Supabase-ready SQL schema for users, locations, tracking requests, origin anchors, and daily traffic logs
- a simple API route that inserts tracking requests into Supabase

## Setup
1. Install dependencies:
   npm install
2. Create a Supabase project and set these environment variables:
   - NEXT_PUBLIC_SUPABASE_URL
   - NEXT_PUBLIC_SUPABASE_ANON_KEY
3. Run the SQL in sql/supabase_schema.sql in your Supabase SQL editor.
4. Start the app:
   npm run dev

## Notes
- The frontend form posts to /api/requests.
- The API route expects the tracking_requests table to exist.
- The backend automation for Overpass, Google Maps, and Open-Meteo can be implemented in a later step using GitHub Actions and Python.
