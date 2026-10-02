/** Small text helpers for showing node params next to their units. */

/**
 * The unit text shown after a param's input, from the catalog's
 * ParamTypeSpec.unit. A "fraction" also shows its percent, e.g.
 * "fraction (100%)", so 1 is not read as 1%. Null when there is no unit.
 */
export function unitLabel(unit: string | undefined, value: string): string | null {
  if (!unit) return null
  if (unit === 'fraction') {
    const n = Number(value)
    if (value.trim() !== '' && Number.isFinite(n)) return `fraction (${formatPercent(n)})`
  }
  return unit
}

/** A fraction as a percent with no float noise: 0.25 → "25%", 0.333 → "33.3%". */
export function formatPercent(fraction: number): string {
  return `${Number((fraction * 100).toFixed(2))}%`
}

/**
 * How a number row shows its value (S32b):
 * - 'percent': the store keeps a fraction, the field shows and takes a
 *   percent (1.0 shows `100`, typing `50` stores 0.5), unit `%`.
 * - 'stop': 0 (or no value) shows a dim `none`.
 */
export type ParamView = 'percent' | 'stop'

/** The field text for a stored value under a view. */
export function viewText(value: unknown, view: ParamView | undefined): string {
  if (value === null || value === undefined) return ''
  if (view === 'percent' && typeof value === 'number' && Number.isFinite(value)) return String(Number((value * 100).toFixed(6)))
  if (view === 'stop' && Number(value) === 0) return ''
  return String(value)
}

/** The value to store for a typed number under a view. */
export function viewValue(n: number, view: ParamView | undefined): number {
  return view === 'percent' ? Number((n / 100).toFixed(10)) : n
}
