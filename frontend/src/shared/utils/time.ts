import { useSyncExternalStore } from 'react'

const TZ_KEY = 'strategylab-timezone'
type TzMode = 'ET' | 'local'

let current: TzMode = (localStorage.getItem(TZ_KEY) as TzMode) || 'ET'
const listeners = new Set<() => void>()

function notify() { listeners.forEach(fn => fn()) }

export function getTimezone(): TzMode { return current }

export function setTimezone(mode: TzMode) {
  current = mode
  localStorage.setItem(TZ_KEY, mode)
  notify()
}

export function useTimezone(): [TzMode, (m: TzMode) => void] {
  const mode = useSyncExternalStore(
    cb => { listeners.add(cb); return () => listeners.delete(cb) },
    () => current,
  )
  return [mode, setTimezone]
}

function tz(): string | undefined {
  return current === 'ET' ? 'America/New_York' : undefined
}

export function fmtDateTimeET(d: string | number | undefined): string {
  if (d === undefined) return '—'
  const date = typeof d === 'number' ? new Date(d * 1000) : new Date(d)
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: tz(),
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date).replace(',', '')
}

export function fmtShortET(d: string): string {
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: tz(),
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date(d))
  } catch { return d }
}

export function fmtTimeET(d: string): string {
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: tz(),
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    }).format(new Date(d))
  } catch { return d }
}

export function tzLabel(): string {
  try {
    const tz = current === 'ET' ? 'America/New_York' : undefined
    return new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'short' })
      .formatToParts(new Date())
      .find(p => p.type === 'timeZoneName')?.value ?? 'Local'
  } catch { return 'Local' }
}

// Shift unix timestamps to the display timezone's wall-clock time by
// reconstructing them as UTC so lightweight-charts displays e.g. 9:30 for NYSE open.
// Date strings (daily+) pass through unchanged.
// Mirrors Chart.tsx toET() — kept in sync with that function.
const _fmtCache = new Map<string, Intl.DateTimeFormat>()
const _localTz = Intl.DateTimeFormat().resolvedOptions().timeZone
function _getFormatter(tzName: string): Intl.DateTimeFormat {
  let f = _fmtCache.get(tzName)
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', {
      timeZone: tzName,
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false,
    })
    _fmtCache.set(tzName, f)
  }
  return f
}

export function toDisplayTime(time: string | number): any {
  if (typeof time !== 'number') return time
  const tzName = current === 'ET' ? 'America/New_York' : _localTz
  const parts = _getFormatter(tzName).formatToParts(new Date(time * 1000))
  const get = (type: string) => parseInt(parts.find(p => p.type === type)?.value ?? '0')
  return Date.UTC(get('year'), get('month') - 1, get('day'), get('hour') % 24, get('minute'), get('second')) / 1000
}
