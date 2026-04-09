/**
 * All timestamps in StrategyLab are displayed in US Eastern Time (ET)
 * to match NYSE trading hours.
 */

/** Full date+time: "2026-04-01 13:35" ET — for backtest trades table */
export function fmtDateTimeET(d: string | number | undefined): string {
  if (d === undefined) return '—'
  const date = typeof d === 'number' ? new Date(d * 1000) : new Date(d)
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date).replace(',', '')
}

/** Short date+time: "Apr 1, 13:35" ET — for journals, order history, scanner */
export function fmtShortET(d: string): string {
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date(d))
  } catch { return d }
}

/** Time only: "13:35:29" ET — for bot activity log */
export function fmtTimeET(d: string): string {
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    }).format(new Date(d))
  } catch { return d }
}
