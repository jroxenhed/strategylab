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
