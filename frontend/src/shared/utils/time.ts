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
