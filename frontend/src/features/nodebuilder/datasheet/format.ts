/**
 * Text formats for the Data Sheet (S25, shared part 0.7).
 *
 * Times: daily "YYYY-MM-DD" strings pass through as given. Intraday unix
 * seconds are shown as wall-clock time `YYYY-MM-DD HH:MM` with the same rule
 * as the chart's `toET()`: `toDisplayTime` in shared/utils/time.ts is that
 * rule, exported (it follows the app's ET / local toggle like the chart).
 * We only print the shifted value; the conversion is not written again here.
 */

import { toDisplayTime } from '../../../shared/utils/time'

/** Thin space, the thousands separator of shared part 0.7. */
export const THIN_SPACE = '\u2009'

const pad = (n: number) => String(n).padStart(2, '0')

/** A bar time as the sheet's time column shows it. */
export function formatSheetTime(t: string | number | null | undefined): string {
  if (t == null) return ''
  if (typeof t !== 'number') return t
  if (!Number.isFinite(t)) return String(t)
  // toDisplayTime returns seconds whose UTC fields read as the wall clock.
  const shifted = toDisplayTime(t) as number
  const d = new Date(shifted * 1000)
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
}

/** An integer with thin-space thousands: 48 213 400. */
export function groupThousands(n: number): string {
  const sign = n < 0 ? '-' : ''
  const digits = String(Math.round(Math.abs(n)))
  return sign + digits.replace(/\B(?=(\d{3})+(?!\d))/g, THIN_SPACE)
}

/**
 * A number cell (shared part 0.7): |v| >= 1000 has no decimals and grouped
 * thousands; anything smaller has 4 decimals. Missing values print `nan`.
 */
export function formatNumber(v: number | null | undefined): string {
  if (v == null || typeof v !== 'number' || Number.isNaN(v)) return 'nan'
  if (!Number.isFinite(v)) return v > 0 ? 'inf' : '-inf'
  if (Math.abs(v) >= 1000) return groupThousands(v)
  return v.toFixed(4)
}

/** The header row count: `1 258 rows`, `31 of 1 258 rows`, or `no rows`. */
export function formatRowCount(total: number, unfiltered: number | null, filtered: boolean): string {
  if (filtered && unfiltered != null) return `${groupThousands(total)} of ${groupThousands(unfiltered)} rows`
  if (total === 0) return 'no rows'
  return `${groupThousands(total)} ${total === 1 ? 'row' : 'rows'}`
}

/** `2.5 % true` for a bool column header. */
export function formatTruePct(trueCount: number, total: number): string {
  if (!(total > 0)) return '0 % true'
  const pct = (trueCount / total) * 100
  return `${pct.toFixed(1)} % true`
}

/** Short text for a histogram edge: few decimals, no trailing noise. */
export function formatEdge(v: number): string {
  if (!Number.isFinite(v)) return String(v)
  if (Math.abs(v) >= 1000) return groupThousands(v)
  return v.toFixed(1)
}

/** A value as plain text for copying (TSV): no grouping, full precision. */
export function copyText(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'number' && Number.isNaN(v)) return 'nan'
  return String(v)
}

/** A key that compares two bar times (a daily string or unix seconds). */
export function timeKey(t: string | number | null | undefined): string {
  if (t == null) return ''
  return typeof t === 'number' ? `n:${t}` : `s:${t}`
}

/** True when `a` comes after `b` (same kind of time only). */
export function timeAfter(a: string | number, b: string | number): boolean {
  if (typeof a === 'number' && typeof b === 'number') return a > b
  return String(a) > String(b)
}
