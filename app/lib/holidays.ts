// Malaysia nationwide public holidays + working-day helpers. Collection skips
// weekends and these holidays; the form uses them to validate the 5-working-day
// cap and to show the user which days will be ignored.
import holidayData from '@/data/public_holidays_my.json';

const HOLIDAYS: Record<string, string> = Object.fromEntries(
  (holidayData.holidays as Array<{ date: string; name: string }>).map((h) => [h.date, h.name]),
);

/** YYYY-MM-DD for a Date, in local time. */
function toKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function holidayName(dateKey: string): string | null {
  return HOLIDAYS[dateKey] ?? null;
}

export function isWeekend(d: Date): boolean {
  const day = d.getDay();
  return day === 0 || day === 6; // Sunday or Saturday
}

export function isWorkingDay(d: Date): boolean {
  return !isWeekend(d) && !HOLIDAYS[toKey(d)];
}

/** Parse a YYYY-MM-DD string as a local date (no timezone drift). */
export function parseDate(value: string): Date | null {
  if (!value) return null;
  const [y, m, d] = value.split('-').map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d);
}

/** Count working days (Mon-Fri, excluding holidays) between two dates inclusive. */
export function workingDaysBetween(startKey: string, endKey: string): number {
  const start = parseDate(startKey);
  const end = parseDate(endKey);
  if (!start || !end || end < start) return 0;
  let count = 0;
  const cursor = new Date(start);
  while (cursor <= end) {
    if (isWorkingDay(cursor)) count += 1;
    cursor.setDate(cursor.getDate() + 1);
  }
  return count;
}

/** Public holidays that fall within the inclusive date range. */
export function holidaysInRange(startKey: string, endKey: string): Array<{ date: string; name: string }> {
  const start = parseDate(startKey);
  const end = parseDate(endKey);
  if (!start || !end || end < start) return [];
  const out: Array<{ date: string; name: string }> = [];
  const cursor = new Date(start);
  while (cursor <= end) {
    const key = toKey(cursor);
    if (HOLIDAYS[key]) out.push({ date: key, name: HOLIDAYS[key] });
    cursor.setDate(cursor.getDate() + 1);
  }
  return out;
}

/** The next working day on or after `from` (default today). */
export function nextWorkingDay(from: Date = new Date()): Date {
  const d = new Date(from.getFullYear(), from.getMonth(), from.getDate());
  while (!isWorkingDay(d)) d.setDate(d.getDate() + 1);
  return d;
}

export function toDateKey(d: Date): string {
  return toKey(d);
}
