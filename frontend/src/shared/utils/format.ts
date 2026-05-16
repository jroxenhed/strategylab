export function fmtUsd(n: number): string {
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

export function fmtPnl(n: number): string {
  const s = Math.abs(n).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
  return n >= 0 ? `+${s}` : `-${s}`
}

export function fmtPct(n: number, decimals = 2): string {
  return `${n >= 0 ? '+' : ''}${n.toFixed(decimals)}%`
}

/**
 * Format a number for display using en-US locale (period decimal separator).
 * Use this instead of .toLocaleString(undefined, ...) or .toLocaleString()
 * to avoid locale-specific decimal/thousand separators in non-display contexts.
 * @param n - the number to format
 * @param maxFractionDigits - maximum fraction digits (default 2)
 */
export function fmtNum(n: number, maxFractionDigits = 2): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: maxFractionDigits }).format(n)
}

/**
 * Parse a user-typed or locale-formatted numeric string into a JS number.
 * Strips currency symbols, spaces, and thousand separators before parsing.
 * Supports both period and comma as decimal separator (e.g. "3,09" → 3.09).
 *
 * Algorithm: after stripping non-numeric chars (except `.` `-`), if the
 * original string contained a comma but no period, treat the last comma as
 * the decimal separator (handles "3,09" and "1.234,56" correctly).
 */
export function parseNumeric(s: string): number {
  const trimmed = s.trim()
  // Detect if comma is used as decimal separator: comma present, no period, or
  // comma appears after any digits as the last separator with 1-2 decimal digits.
  const hasComma = trimmed.includes(',')
  const hasPeriod = trimmed.includes('.')
  let normalized: string
  if (hasComma && !hasPeriod) {
    // e.g. "3,09" or "1.234" is impossible here since no period
    // Replace comma with period for decimal, drop other separators
    normalized = trimmed.replace(/[^0-9,.\-]/g, '').replace(',', '.')
  } else if (hasComma && hasPeriod) {
    // e.g. "1.234,56" (European) or "1,234.56" (en-US)
    // If comma comes after period: en-US thousand sep → strip commas
    // If period comes after comma: European → strip periods, replace comma with period
    const lastComma = trimmed.lastIndexOf(',')
    const lastPeriod = trimmed.lastIndexOf('.')
    if (lastComma > lastPeriod) {
      // European: "1.234,56"
      normalized = trimmed.replace(/[^0-9,\-]/g, '').replace(',', '.')
    } else {
      // en-US: "1,234.56"
      normalized = trimmed.replace(/[^0-9.\-]/g, '')
    }
  } else {
    // No comma — strip any non-numeric chars except period and minus
    normalized = trimmed.replace(/[^0-9.\-]/g, '')
  }
  return Number.parseFloat(normalized)
}
