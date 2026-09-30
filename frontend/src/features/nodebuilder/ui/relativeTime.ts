/**
 * Relative time for the node builder (surfaces-w1-w4.md 0.6):
 * `just now` (< 10 s), `N s ago` (< 60 s), `N min ago` (< 60 min),
 * `N h ago` (< 24 h), `yesterday`, else `YYYY-MM-DD`.
 *
 * "yesterday" means the day before today on the local calendar. Dates are
 * local too. A time a little in the future (clock skew between browser and
 * server) reads `just now`.
 *
 * `useRelativeTime` re-renders every 30 s while the page is visible.
 */
import { useEffect, useState } from 'react'

export type TimeInput = Date | string | number

/** How often the hook refreshes the text. */
export const RELATIVE_TIME_REFRESH_MS = 30_000

function toMs(t: TimeInput): number {
  return t instanceof Date ? t.getTime() : typeof t === 'number' ? t : new Date(t).getTime()
}

const pad = (n: number) => String(n).padStart(2, '0')

/** Local `YYYY-MM-DD`. */
export function formatDate(t: TimeInput): string {
  const d = new Date(toMs(t))
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** Local `YYYY-MM-DD HH:MM:SS`, for a `title` next to a relative time. */
export function formatFullTimestamp(t: TimeInput): string {
  const d = new Date(toMs(t))
  if (Number.isNaN(d.getTime())) return ''
  return `${formatDate(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** The relative text for `then`, seen at `now`. Empty for an unreadable time. */
export function formatRelativeTime(then: TimeInput, now: TimeInput = Date.now()): string {
  const t = toMs(then)
  const n = toMs(now)
  if (Number.isNaN(t) || Number.isNaN(n)) return ''
  const sec = Math.floor((n - t) / 1000)
  if (sec < 10) return 'just now'
  if (sec < 60) return `${sec} s ago`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} min ago`
  const hours = Math.floor(min / 60)
  if (hours < 24) return `${hours} h ago`
  const yesterday = new Date(n)
  yesterday.setDate(yesterday.getDate() - 1)
  if (formatDate(t) === formatDate(yesterday)) return 'yesterday'
  return formatDate(t)
}

/**
 * The relative text for `then`, kept fresh: every 30 s while the page is
 * visible, and at once when the page becomes visible again.
 */
export function useRelativeTime(then: TimeInput | null | undefined): string {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (then == null) return
    const hidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden'
    const tick = () => {
      if (!hidden()) setNow(Date.now())
    }
    const timer = setInterval(tick, RELATIVE_TIME_REFRESH_MS)
    document.addEventListener('visibilitychange', tick)
    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [then])
  return then == null ? '' : formatRelativeTime(then, now)
}
